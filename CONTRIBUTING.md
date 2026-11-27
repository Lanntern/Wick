# Contributing to Wick

Thanks for helping! Bug reports, ideas and pull requests are all welcome.

## Running from source

Requires Python 3.11 or newer.

```bash
pip install -e .
```

Then get the encoders:

* **Windows:** `python scripts/fetch_tools.py` downloads ffmpeg/ffprobe, cjxl/djxl/cjpegli and gifsicle into `tools/`.
* **Linux:** `sudo apt install ffmpeg gifsicle`, then `python scripts/fetch_tools.py --libjxl` for cjxl/djxl/cjpegli 0.11
  (distro libjxl is often too old). `--ffmpeg` downloads the static FFmpeg build CI uses, instead of the distro's.

Start the app with `wick`.

## Reporting problems

- **Bugs:** open an issue with what you did, what happened, and what you expected. Settings → About → About
  Wick → **Copy Versions** gives the version details worth pasting in.
- **Security problems:** please don't open a public issue; see [SECURITY.md](SECURITY.md).

## Pull requests

1. **Sign the Contributor License Agreement.** On your first pull request a bot asks you to sign
   [CLA.md](CLA.md) by posting a comment; that one comment covers all your future contributions (signatures are
   kept on the `cla-signatures` branch). You keep the
   copyright in your work. The agreement lets the maintainer keep the project's licensing flexible (for
   example, move to a newer license) without tracking down every contributor. Pull requests can't be merged
   until it's signed.
2. **Keep it focused.** One change per pull request, with a short description of what it changes and why.
   For a bigger change, open an issue first so we can agree on the approach.
3. **Run the checks** (CI runs them too):

   ```bash
   pip install --require-hashes -r requirements.txt
   pip install --no-deps . pytest ruff==0.16.9 mypy==2.3.1
   ruff check .
   mypy wick
   python -m pytest
   ```

   Tests that need the encoders skip themselves when the tools aren't installed;
   `python scripts/fetch_tools.py` downloads them (see the README).
4. **Add a test** for a bug fix or new behavior where you can.
5. **Be careful with people's files.** Wick replaces and deletes files, so anything touching that path
   needs to keep the existing guarantees: verify before replacing, originals to the Recycle Bin, everything
   undoable.

## Development notes

```bash
pip install -e ".[dev]"
python -m pytest          # unit tests + real round trips (needs the encoders)
```

CI (`.github/workflows/build.yml`) lints, then runs the tests on Linux and on Windows (including the real Recycle Bin restore and offscreen GUI tests that drive the main window). Pull requests stop there. Pushes to `main`, `v*` tags and manual runs also build the `.exe` with PyInstaller and the installer, smoke-test both, and keep them as run artifacts; a `v*` tag attaches them to the GitHub release. Publishing a release, and the Scoop and winget packages: [docs/releasing.md](docs/releasing.md).

Data lives in `%LOCALAPPDATA%\Wick` (`~/.local/share/wick` on Linux): run history (`runs/`), presets (`presets.json`), the file index that makes repeat scans fast (`index.sqlite`, safe to delete) and a small rotating log (`logs/`). Settings → Diagnostics opens the log folder or copies a summary for bug reports. `WICK_DATA_DIR` and `WICK_SETTINGS_FILE` override the locations (used by the tests).

Layout:

```
wick/
  scanner.py       walk folders, skip system/program folders, probe videos
  index.py         cache of probed file details (fast repeat scans)
  presets.py       built-in and user presets
  quality.py       SSIMULACRA2 / VMAF / SSIM scoring and the Smart quality search
  log.py           log file, crash hooks, diagnostics
  plan.py          per-file decision + savings estimate
  images.py        all image routes (cjxl, cjpegli, mozjpeg, oxipng, imagequant, gifsicle, Pillow) + verification
  metadata.py      lossless EXIF/XMP editing in JPEG, PNG, WebP and JPEG XL
  video.py         ffmpeg AV1/HEVC (NVENC or CPU) + verification
  audio.py         FLAC (sample-exact check), Opus/AAC/MP3, mono detection, CD quality, tags and album art
  engine.py        run a job: convert → verify → swap → journal; undo
  trash_restore.py restore from the Windows Recycle Bin / freedesktop Trash
  journal.py       run history (JSON lines in %LOCALAPPDATA%\Wick)
  cli.py           command line
  gui/             PySide6 desktop app (compress, preview, watch folders, history, settings, tray)
scripts/fetch_tools.py   download Windows encoders
```

### Code conventions

The lint job checks most of these; the rest are here so a change doesn't have to learn them in review.

- **Before each commit:** `pip install pre-commit` (in the same environment as Wick's dependencies), then
  `pre-commit install` once. Ruff, vulture and the code rules then run on every commit, and the type checks
  before every push, with the tool versions CI uses (`.pre-commit-config.yaml`).
- **Checks:** `ruff check .`, `vulture`, and mypy once per system, run with the project's own Python (`python -m mypy`, so it sees PySide6 and the other libraries):
  `python -m mypy wick --platform linux --always-false IS_WINDOWS --always-false IS_MAC` (then `win32` with
  `--always-true IS_WINDOWS`, and `darwin` with `--always-true IS_MAC`). Each run skips the other systems'
  branches, so system checks are written `sys.platform == "win32"` (or the module's `IS_WINDOWS` / `IS_MAC`),
  never `os.name`, and a Windows-only helper starts with `if sys.platform != "win32": return ...`.
- **Imports at the top of the module**, except:
  - heavy libraries (Pillow, numpy, pikepdf, fontTools, Qt outside `gui/`, the encoders' Python bindings),
    loaded where they're used so the window opens quickly;
  - `ctypes`, `winreg` and other system modules used only on one system;
  - pages and dialogs built when they're first opened, and modules that would close an import cycle;
  - names the tests replace on their module (`patch.object(video, "verify_video")`): imported where they're
    called, they pick up the replacement.
- **Private names stay private:** `_name` is for its own module. If another module needs it, give it a
  public name (`tests/test_code_rules.py` checks).
- **Errors:** catch the error you expect (`OSError`, `sqlite3.Error`, `tools.ToolError`...). A catch-all
  (`except Exception`) is for code that reads untrusted files or third-party libraries that can raise anything;
  it carries `# noqa: BLE001 - why`, and writes the error to the log (`log.ignored("where")`), re-raises, or
  hands it on as a message. A silent one fails `tests/test_code_rules.py`.
- **Unused arguments** are removed, or start with `_` when the caller dictates the signature (a Qt override, a
  callback, one entry of a table of handlers); ruff's ARG rule checks (not in tests, where fixtures and fakes
  take arguments they don't use).
- **Functions under 20 branches** (ruff C901). A long function becomes named steps (or a small class when the
  steps share state), not a `noqa`.
- **Tests that fake another system** change `sys.platform` (`monkeypatch.setattr(sys, "platform", "linux")`);
  changing `os.name` does nothing, and `tests/test_code_rules.py` rejects it.
- **Tests never open a real dialog.** Anything that waits for an answer (`exec()`, `QFileDialog.get...`,
  `QMessageBox`, Windows' message box) is answered "cancel" and fails the test that opened it; replace the dialog
  in the test first (`monkeypatch.setattr(SomeDialog, "exec", ...)`).
- **Folder walks** go through `wick/walk.py`, so links, junctions and unreadable folders are handled the same way
  everywhere.

## How bundled components are kept up to date

Wick opens files with decoders it bundles (FFmpeg, libjxl, Pillow with its WebP/JPEG libraries, libheif,
qpdf). Those get security fixes, so:

* **Builds are pinned and audited.** `requirements.txt` holds every Python library's exact version and checksum
  (installed with `--require-hashes`); `pip-audit` checks them for known vulnerabilities whenever they change
  and every week.
  The command-line tools are exact releases checked against recorded SHA-256 checksums
  (`scripts/fetch_tools.py`); a changed download is refused.
* **Maintainers hear about new versions.** Dependabot opens pull requests for new library versions, and a weekly job
  (`scripts/check_updates.py`) opens an issue when FFmpeg, libjxl or gifsicle has a newer release or a library
  has a new advisory.

## License

Wick is licensed under the GNU General Public License, version 3 only ([LICENSE](LICENSE)). By
contributing you agree to [CLA.md](CLA.md).

The Wick name and icon are not covered by the code's license; see the end of the
[README](README.md#license).
