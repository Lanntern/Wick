"""Put the encoders inside Wick.app, so nothing needs installing on the Mac it runs on.

Takes Homebrew's builds (ffmpeg, ffprobe, cjxl, djxl, gifsicle and libjxl's extras where present), copies them
into Wick.app/Contents/MacOS/tools (where tools.find_tool looks first), and with dylibbundler copies every library
they load from Homebrew next to them, pointing the programs at those copies. Then checks that nothing still
points into Homebrew's folders, signs each file ad hoc, and copies the projects' license files.

Usage: python packaging/mac_tools.py dist/Wick.app   (after `brew install ffmpeg jpeg-xl gifsicle dylibbundler`)
"""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

PROGRAMS = {"ffmpeg": "ffmpeg", "ffprobe": "ffmpeg", "cjxl": "jpeg-xl", "djxl": "jpeg-xl", "gifsicle": "gifsicle",
            "cjpegli": "jpeg-xl", "ssimulacra2": "jpeg-xl", "butteraugli_main": "jpeg-xl"}
REQUIRED = {"ffmpeg", "ffprobe", "cjxl", "djxl"}
HOMEBREW = ("/opt/homebrew/", "/usr/local/")


def brew_prefix() -> Path:
    return Path(subprocess.run(["brew", "--prefix"], capture_output=True, text=True, check=True).stdout.strip())


def linked(binary: Path) -> list[str]:
    out = subprocess.run(["otool", "-L", str(binary)], capture_output=True, text=True, check=True).stdout
    return [line.strip().split(" (")[0] for line in out.splitlines()[1:] if line.strip()]


def main(app: str) -> int:
    bundle = Path(app)
    tools = bundle / "Contents" / "MacOS" / "tools"
    libs = tools / "lib"
    tools.mkdir(parents=True, exist_ok=True)
    prefix = brew_prefix()
    copied = []
    for name, formula in PROGRAMS.items():
        src = prefix / "bin" / name
        if not src.exists():
            if name in REQUIRED:
                print(f"missing {src}: brew install {formula}")
                return 1
            continue
        dst = tools / name
        shutil.copy2(src.resolve(), dst)
        dst.chmod(0o755)
        copied.append(dst)
    args = ["dylibbundler", "-of", "-b", "-cd", "-d", str(libs), "-p", "@executable_path/lib/"]
    for exe in copied:
        args += ["-x", str(exe)]
    subprocess.run(args, check=True)
    files = copied + sorted(libs.glob("*.dylib"))
    left = [(f.name, dep) for f in files for dep in linked(f) if dep.startswith(HOMEBREW)]
    if left:
        print("still pointing into Homebrew:", *left, sep="\n  ")
        return 1
    for f in files:
        subprocess.run(["codesign", "--force", "--sign", "-", str(f)], check=True, capture_output=True)
    for formula in sorted(set(PROGRAMS[e.name] for e in copied)):
        opt = prefix / "opt" / formula
        for lic in [*opt.glob("LICENSE*"), *opt.glob("COPYING*")][:3]:
            shutil.copy2(lic, tools / f"LICENSE-{formula}-{lic.name}")
    for exe in copied:                                  # each one starts, with only what's in the app
        proc = subprocess.run([str(exe), "-version" if exe.name in ("ffmpeg", "ffprobe") else "--version"],
                              capture_output=True, text=True, timeout=60)
        print(f"{exe.name}: exit {proc.returncode}")
        if exe.name in REQUIRED and proc.returncode != 0:
            print(proc.stderr[-2000:])
            return 1
    size = sum(f.stat().st_size for f in tools.rglob("*") if f.is_file())
    print(f"{len(copied)} programs and {len(files) - len(copied)} libraries in {tools} ({size / 1e6:.0f} MB)")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1] if len(sys.argv) > 1 else "dist/Wick.app"))
