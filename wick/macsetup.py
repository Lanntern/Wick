"""macOS: getting the encoders when they aren't already there.

The Wick.app from GitHub carries its own (packaging/mac_tools.py puts them inside the app), so this is for Wick run
from its source, or an app whose encoders were removed: Wick installs the missing ones with Homebrew, the usual
package manager on a Mac, after asking. Without Homebrew it says how to get it (installing Homebrew needs an
administrator's password in Terminal, which an app shouldn't ask for).
"""

from __future__ import annotations

import os
import subprocess
import sys
import threading
from collections.abc import Callable
from pathlib import Path

from . import detected, tools

# Each Homebrew package and the programs Wick uses from it.
FORMULAE = {"ffmpeg": ("ffmpeg", "ffprobe"), "jpeg-xl": ("cjxl", "djxl"), "gifsicle": ("gifsicle",)}
BREW_LOCATIONS = (Path("/opt/homebrew/bin/brew"), Path("/usr/local/bin/brew"))


def applies() -> bool:
    return sys.platform == "darwin"


def brew() -> Path | None:
    """Homebrew's ``brew``, if it's installed (Apple silicon or Intel location)."""
    return next((p for p in BREW_LOCATIONS if p.is_file() and os.access(p, os.X_OK)), None)


def missing_formulae() -> list[str]:
    """The Homebrew packages that would provide the encoders Wick can't find."""

    return [f for f, programs in FORMULAE.items() if any(tools.find_tool(p) is None for p in programs)]


class InstallFailed(RuntimeError):
    pass


def install(formulae: list[str], progress: Callable[[str], None] | None = None,
            cancel: threading.Event | None = None) -> None:
    """``brew install`` the packages, reporting each line Homebrew prints; raises InstallFailed with its last
    lines if it fails."""
    exe = brew()
    if exe is None:
        raise InstallFailed("Homebrew isn't installed")
    env = dict(os.environ, HOMEBREW_NO_ENV_HINTS="1", HOMEBREW_NO_INSTALL_CLEANUP="1",
               NONINTERACTIVE="1", HOMEBREW_NO_AUTO_UPDATE="1")
    proc = subprocess.Popen([str(exe), "install", *formulae], stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                            stdin=subprocess.DEVNULL, text=True, errors="replace", env=env)
    tail: list[str] = []
    assert proc.stdout is not None
    for line in proc.stdout:
        line = line.rstrip()
        if line:
            tail = (tail + [line])[-8:]
            if progress is not None:
                progress(line)
        if cancel is not None and cancel.is_set():
            proc.terminate()
            proc.wait(10)
            raise InstallFailed("Stopped")
    if proc.wait() != 0:
        raise InstallFailed("\n".join(tail) or f"brew exited with {proc.returncode}")

    detected.forget()          # (look again: the new programs are in Homebrew's folder now)


def homebrew_hint() -> str:
    return ("Wick installs its encoders with Homebrew, the usual package manager on a Mac. Install Homebrew from "
            "brew.sh (it asks for your password in Terminal), then come back here.")


__all__ = ["applies", "brew", "missing_formulae", "install", "InstallFailed", "FORMULAE", "homebrew_hint"]
