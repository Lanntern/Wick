"""Put the encoders inside Wick.app, so nothing needs installing on the Mac it runs on.

Takes Homebrew's builds (ffmpeg, ffprobe, cjxl, djxl, gifsicle and libjxl's extras where present), copies them
into Wick.app/Contents/MacOS/tools (where tools.find_tool looks first), copies every library they load from
Homebrew (and the libraries those load, all the way down) into tools/lib, and points each reference at the copy
(install_name_tool: @rpath, with the programs' rpath @executable_path/lib and the libraries' @loader_path). Then
checks that nothing still points into Homebrew's folders, signs each file ad hoc, and copies the license files.

Usage: python packaging/mac_tools.py dist/Wick.app   (after `brew install ffmpeg jpeg-xl gifsicle`)
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
    deps = [line.strip().split(" (")[0] for line in out.splitlines()[1:] if line.strip()]
    if binary.suffix == ".dylib" and deps:
        deps = deps[1:]                                   # (a library's first line is its own name)
    return deps


def rpaths(binary: Path) -> list[str]:
    out = subprocess.run(["otool", "-l", str(binary)], capture_output=True, text=True, check=True).stdout.splitlines()
    found = []
    for i, line in enumerate(out):
        if line.strip() == "cmd LC_RPATH":
            for follow in out[i + 1:i + 4]:
                if follow.strip().startswith("path "):
                    found.append(follow.strip()[5:].split(" (offset")[0])
    return found


def resolve(dep: str, referrer: Path, origin: Path) -> Path | None:
    """Where a reference points: absolute, or @loader_path / @executable_path / @rpath relative (the referrer's real
    location, the program's, and the referrer's rpaths)."""
    here = referrer.resolve().parent
    if dep.startswith("@loader_path/"):
        candidates = [here / dep[len("@loader_path/"):]]
    elif dep.startswith("@executable_path/"):
        candidates = [origin.resolve().parent / dep[len("@executable_path/"):]]
    elif dep.startswith("@rpath/"):
        rest = dep[len("@rpath/"):]
        candidates = []
        for rp in rpaths(referrer.resolve()):
            rp = rp.replace("@loader_path", str(here)).replace("@executable_path", str(origin.resolve().parent))
            candidates.append(Path(rp) / rest)
    else:
        candidates = [Path(dep)]
    return next((c.resolve() for c in candidates if c.exists()), None)


def system(dep: str) -> bool:
    return dep.startswith(("/usr/lib/", "/System/"))


def bundle_libraries(programs: list[Path], libs: Path) -> list[Path]:
    """Copy every non-system library the programs need into ``libs`` and point all references at the copies."""
    libs.mkdir(exist_ok=True)
    originals: dict[str, Path] = {}             # copied name -> where it came from
    todo = [(p, Path(SOURCES[p.name]), Path(SOURCES[p.name])) for p in programs]   # (copy, original, program)
    seen = set()
    while todo:
        file, original, origin = todo.pop()
        if file in seen:
            continue
        seen.add(file)
        for dep in linked(original):
            if system(dep):
                continue
            src = resolve(dep, original, origin)
            if src is None:
                raise SystemExit(f"{original.name}: can't find {dep}")
            name = Path(dep).name
            copy = libs / name
            if name not in originals:
                originals[name] = src
                shutil.copy2(src, copy)
                copy.chmod(0o755)
                todo.append((copy, src, origin))
            subprocess.run(["install_name_tool", "-change", dep, f"@rpath/{name}", str(file)], check=True,
                           capture_output=True)
    for copy in libs.glob("*.dylib"):
        subprocess.run(["install_name_tool", "-id", f"@rpath/{copy.name}", str(copy)], check=True, capture_output=True)
    for file in [*programs, *libs.glob("*.dylib")]:
        for rp in rpaths(file):
            subprocess.run(["install_name_tool", "-delete_rpath", rp, str(file)], capture_output=True)
        own = "@executable_path/lib" if file in programs else "@loader_path"
        subprocess.run(["install_name_tool", "-add_rpath", own, str(file)], check=True, capture_output=True)
    return sorted(libs.glob("*.dylib"))


SOURCES: dict[str, str] = {}                     # program name -> its file in Homebrew


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
        SOURCES[name] = str(src.resolve())
        copied.append(dst)
    files = copied + bundle_libraries(copied, libs)
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
