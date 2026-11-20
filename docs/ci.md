# CI checks

The required job names stay `lint`, `linux` and `windows`. Both platforms run the full test suite.
New PR pushes cancel superseded runs; packaging runs on main, version tags and manual dispatch only.

## Where time is saved

- Dependencies install with uv (`uv pip install`, the same pinned versions and `--require-hashes` checks as
  pip, several times faster); its download cache is keyed on the requirements files.
- mypy restores its incremental cache from a compatible earlier run. The key includes its pinned version,
  runtime requirements and configuration; source changes are checked by mypy itself.
- Windows starts Everything before installing Python dependencies and runs the GUI tests before waiting for its
  index, so indexing overlaps that work.
- Core/integration tests run in parallel, one worker per CPU (pytest-xdist, `--dist loadgroup`).
- Core/integration tests and the main GUI/style/layout tests run in separate Python processes, without
  repeating dependency installation. This limits state leaking between media workers and the GUI tests.

The first cache fill is not representative of later runs. Runner queue time is outside these caches;
no runtime reduction is promised for an overloaded GitHub runner.

## Progress and hangs

CI prints every test name with unbuffered output and saves JUnit XML reports for seven days, including
on failures when reports were produced. The GUI process still runs after a core-test failure, provided
dependency installation succeeded and the workflow was not cancelled.

After five minutes in one test, the timeout watchdog dumps thread stacks and terminates the test
process with a failure. The native faulthandler watchdog is also configured to exit on timeout, for
C-extension hangs. Step time limits remain a backstop if teardown has disabled the native timer and
blocked the Python watchdog. Termination can prevent a final JUnit report; the test name and stack dump remain
in the Actions log. The step and job time limits are additional backstops.

Everything and ReFS integration retain their existing optional setup behavior. When Everything setup
succeeds, its real integration test is required; otherwise the test explicitly skips. ReFS coverage runs
when the runner can create the test volume.

## Parallel core tests

Each worker is its own process, so it has its own History and app data (`tests/conftest.py` makes a sandbox
per process) and its own Qt application. Recycle Bin tests only look up the files they sent, by their unique
original paths. What's still shared across processes is grouped: tests that write the signed-in user's registry
(Explorer menus, Default apps, notifications, the jump list) carry `xdist_group("windows-shell")`, so one worker
runs them one after another. A new test that changes shared Windows state needs that marker too (or its own
group). The GUI, style and layout tests stay in one process.
