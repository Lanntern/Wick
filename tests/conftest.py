import os
import subprocess
import tempfile
from pathlib import Path

import pytest

# Isolate app data and (on Linux) the Trash before anything imports send2trash,
# which reads XDG_DATA_HOME at import time.
_SANDBOX = Path(tempfile.mkdtemp(prefix="wick-tests-"))
os.environ["WICK_DATA_DIR"] = str(_SANDBOX / "appdata")
os.environ["WICK_NO_NETWORK"] = "1"      # no automatic update checks while testing
os.environ["WICK_DECODER_TESTS"] = "1"   # the decoder worker's test-only functions (tests/test_sandbox.py)
if os.name != "nt":
    os.environ["XDG_DATA_HOME"] = str(_SANDBOX / "xdg")
    if not (os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY")):
        # No screen: Qt would abort the first test that starts it. (In one process an earlier test's offscreen
        # app often hid this; with parallel workers any test can be the first.)
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from wick import tools  # noqa: E402


def pytest_collection_modifyitems(items):
    """Tests marked ``runs_long`` first: with parallel workers, a long test that starts last leaves the others
    idle while it finishes. (Every worker sorts the same way, so they still agree on the order.)"""
    items.sort(key=lambda item: item.get_closest_marker("runs_long") is None)


def have(*names: str) -> bool:
    return all(tools.find_tool(n) for n in names)


needs_image_tools = pytest.mark.skipif(
    not have("cjxl", "djxl", "ffmpeg", "ffprobe"), reason="image tools not installed")
needs_gifsicle = pytest.mark.skipif(not have("gifsicle"), reason="gifsicle not installed")
needs_ffmpeg = pytest.mark.skipif(not have("ffmpeg", "ffprobe"), reason="ffmpeg not installed")


def _pandoc_works() -> bool:
    """Pandoc that can convert with --sandbox (distribution builds keep data files it then can't read)."""
    exe = tools.find_tool("pandoc")
    if not exe:
        return False
    p = subprocess.run([exe, "-f", "markdown", "-t", "docx", "-o", os.devnull, "--sandbox"], input=b"# x",
                       capture_output=True)
    return p.returncode == 0


needs_pandoc = pytest.mark.skipif(not _pandoc_works(), reason="no Pandoc that works with --sandbox")


def ffmpeg(*args: str, cwd: Path | None = None) -> None:
    subprocess.run([tools.require("ffmpeg"), "-v", "error", "-y", *args], check=True, cwd=cwd)


@pytest.fixture
def media_dir(tmp_path: Path) -> Path:
    # Non-ASCII folder name on purpose: encoders must cope with it.
    d = tmp_path / "Bilder åäö"
    d.mkdir()
    return d
