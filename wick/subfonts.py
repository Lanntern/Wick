"""Fonts inside MKV videos, cut down to what the subtitles use.

Styled subtitles (ASS/SSA) bring their own fonts as attachments, often several megabytes of Chinese or Japanese
fonts for one episode's lines. This keeps, of each font, only the characters the subtitles draw with it (plus
ASCII, Latin-1 and common punctuation, so a quick edit in another tool still works), and leaves out fonts no
subtitle mentions. A font whose licence forbids subsetting is kept whole; so is any font that can't be read or
checked. Nothing is decided by guessing: when a subtitle track can't be read, every font stays as it is.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

from . import tools
from .probe import StreamInfo, VideoInfo

FONT_TYPES = {"application/x-truetype-font", "application/x-font-ttf", "application/x-font-otf",
              "application/vnd.ms-opentype", "application/font-sfnt", "application/x-font-opentype",
              "font/ttf", "font/otf", "font/sfnt", "font/collection"}
FONT_EXTS = {".ttf", ".otf", ".ttc", ".otc"}
ASS_CODECS = {"ass", "ssa"}
# Always kept: printable ASCII, Latin-1 and general punctuation (quotes, dashes, ellipsis).
BASE = {*range(0x20, 0x7F), *range(0xA0, 0x100), *range(0x2000, 0x2070)}
MIN_FONT_BYTES = 1024 ** 2      # Plan: below this much font, a remux for the fonts alone isn't worth it


def is_font(st: StreamInfo) -> bool:
    return st.kind == "attachment" and (st.mimetype in FONT_TYPES or Path(st.filename).suffix.lower() in FONT_EXTS)


def font_bytes(info: VideoInfo) -> int:
    return sum(st.size for st in info.streams if is_font(st))


# --- Which characters each font draws ------------------------------------------------------------------------

_OVERRIDE = re.compile(r"\{([^}]*)\}")
_TAG = re.compile(r"\\(fn|r|p)([^\\]*)")


def _fields(line: str, header: str, count: int) -> list[str]:
    return [f.strip() for f in line[len(header):].split(",", count - 1)]


def used_characters(ass: str) -> dict[str, set[int]]:
    """{font name (lower case): characters drawn with it} for one ASS/SSA script."""
    styles: dict[str, str] = {}
    style_fmt: list[str] = []
    event_fmt: list[str] = []
    used: dict[str, set[int]] = {}
    section = ""
    for raw in ass.splitlines():
        line = raw.strip()
        if line.startswith("[") and line.endswith("]"):
            section = line.lower()
            continue
        if line.lower().startswith("format:"):
            names = [f.strip().lower() for f in line[7:].split(",")]
            if "styles" in section:
                style_fmt = names
            elif "events" in section:
                event_fmt = names
        elif line.startswith("Style:") and style_fmt:
            parts = _fields(line, "Style:", len(style_fmt))
            if len(parts) == len(style_fmt):
                styles[parts[style_fmt.index("name")].lower()] = _name(parts[style_fmt.index("fontname")])
        elif line.startswith("Dialogue:") and event_fmt:
            parts = _fields(line, "Dialogue:", len(event_fmt))
            if len(parts) != len(event_fmt):
                continue
            style = parts[event_fmt.index("style")].lower()
            _scan(parts[-1], styles.get(style, styles.get("default", "")), styles, used)
    return used


def _name(font: str) -> str:
    return font.strip().lstrip("@").lower()          # ("@Font": the same font, drawn vertically)


def _scan(text: str, font: str, styles: dict[str, str], used: dict[str, set[int]]) -> None:
    base, drawing, pos = font, False, 0
    for m in [*_OVERRIDE.finditer(text), None]:
        end = m.start() if m else len(text)
        if not drawing and font:
            chunk = text[pos:end].replace("\\N", "").replace("\\n", "").replace("\\h", " ")
            used.setdefault(font, set()).update(ord(c) for c in chunk)
        if m is None:
            break
        pos = m.end()
        for tag, value in _TAG.findall(m.group(1)):
            value = value.strip()
            if tag == "fn":
                font = _name(value) if value else base
            elif tag == "r":
                font = styles.get(value.lower(), base) if value else base
            elif tag == "p" and value.isdigit():
                drawing = int(value) > 0                 # (vector drawing commands, not text)


# --- Fonts ---------------------------------------------------------------------------------------------------

def _names(font) -> set[str]:
    """Every name a subtitle may call this font by: family, typographic family, full and PostScript name."""
    return {str(r.toUnicode()).strip().lower() for r in font["name"].names
            if r.nameID in (1, 4, 6, 16)} if "name" in font else set()


def _may_subset(font) -> bool:
    fs_type = font["OS/2"].fsType if "OS/2" in font else 0
    return not fs_type & 0x0302          # (restricted licence, no subsetting, bitmap embedding only)


def subset(src: Path, dst: Path, chars: set[int]) -> bool:
    """Write ``src`` cut down to ``chars`` (plus BASE) to ``dst``. False when it may not or need not be cut, or
    the result doesn't hold every character the original had of those."""
    from fontTools import subset as ft_subset
    from fontTools.ttLib import TTFont

    with TTFont(src, lazy=False) as font:
        if not _may_subset(font):
            return False
        have = set(font.getBestCmap() or {})
        want = (chars | BASE) & have
        if len(want) >= len(have):
            return False
        opts = ft_subset.Options()
        opts.layout_features = ["*"]
        opts.name_IDs = ["*"]
        opts.name_languages = ["*"]
        opts.name_legacy = True
        opts.notdef_outline = True
        opts.legacy_kern = True
        opts.symbol_cmap = True
        sub = ft_subset.Subsetter(opts)
        sub.populate(unicodes=sorted(want))
        sub.subset(font)
        font.save(dst)
    with TTFont(dst) as back:                             # read back: every wanted character is still there
        if not want <= set(back.getBestCmap() or {}):
            return False
    return dst.stat().st_size < src.stat().st_size


@dataclass
class FontPlan:
    """What happens to a video's fonts. ``replace``: the font attachments (input stream indexes) taken out;
    ``attach``: (file, name, type) put back in their place, in order. ``saved``: bytes."""
    replace: list[int] = field(default_factory=list)
    attach: list[tuple[Path, str, str]] = field(default_factory=list)
    cut: int = 0
    dropped: int = 0
    pictures: int = 0                   # cover pictures stored smaller (same pixels)
    covers: dict = field(default_factory=dict)   # MP4: {cover stream index: the picture stored smaller}
    saved: int = 0

    def note(self) -> str:
        parts = []
        if self.cut:
            parts.append(f"{self.cut} subtitle font{'s' if self.cut != 1 else ''} cut to the characters used")
        if self.dropped:
            parts.append(f"{self.dropped} unused font{'s' if self.dropped != 1 else ''} left out")
        if self.pictures:
            parts.append(f"{'cover picture' if self.pictures == 1 else f'{self.pictures} cover pictures'} stored "
                         "smaller (same pixels)")
        return ", ".join(parts)


def _subtitle_text(src: Path, index: int) -> str:
    ffmpeg = tools.require("ffmpeg")
    p = tools.run([ffmpeg, "-hide_banner", "-nostdin", "-v", "error", "-i", str(src), "-map", f"0:{index}",
                   "-c", "copy", "-f", "ass", "-"], timeout=300, check=False, text=False)
    if p.returncode != 0:
        raise RuntimeError(f"couldn't read subtitle track {index}")
    return p.stdout.decode("utf-8-sig", "replace")


def is_picture(st: StreamInfo) -> bool:
    """A JPEG or PNG attachment (a cover, a poster)."""
    return st.kind == "attachment" and (st.mimetype in ("image/jpeg", "image/png")
                                        or Path(st.filename).suffix.lower() in (".jpg", ".jpeg", ".png"))


def _dump(src: Path, fonts: list[StreamInfo], folder: Path) -> dict[int, Path]:
    """The attachments as files, named by stream index (the names inside the video aren't trusted)."""
    ffmpeg = tools.require("ffmpeg")
    out = {st.index: folder / f"att{st.index}{_safe_ext(st)}" for st in fonts}
    args = [ffmpeg, "-hide_banner", "-nostdin", "-v", "error", "-y"]
    for index, path in out.items():
        args += [f"-dump_attachment:{index}", str(path)]
    args += ["-i", str(src), "-t", "0", "-f", "null", "-"]
    tools.run(args, timeout=300, check=False)            # (ffmpeg may complain about the output; the files count)
    return {i: p for i, p in out.items() if p.is_file() and p.stat().st_size}


def _safe_ext(st: StreamInfo) -> str:
    ext = Path(st.filename).suffix.lower()
    return ext if ext in FONT_EXTS | {".jpg", ".jpeg", ".png"} else ".png" if "png" in st.mimetype else ".bin"


def plan(src: Path, info: VideoInfo, subs: list[StreamInfo], folder: Path, fonts_too: bool = True) -> FontPlan | None:
    """The plan for ``src``'s attachments, given the subtitle tracks kept: fonts cut (``fonts_too``) and cover
    pictures stored smaller. None when nothing would change."""
    result = _fonts(src, info, subs, folder) if fonts_too else None
    result = _pictures(src, info, folder, result or FontPlan())
    return result if result.cut or result.dropped or result.pictures else None


def mp4_covers(src: Path, info: VideoInfo, folder: Path) -> FontPlan | None:
    """MP4/MOV cover pictures (attached picture streams) stored smaller with the same pixels."""
    from .documents import shrink_jpeg, shrink_png

    result = FontPlan()
    ffmpeg = tools.require("ffmpeg")
    for st in info.streams:
        if not (st.attached_pic and st.codec in ("mjpeg", "png")):
            continue
        p = tools.run([ffmpeg, "-hide_banner", "-nostdin", "-v", "error", "-i", str(src), "-map", f"0:{st.index}",
                       "-c", "copy", "-f", "image2pipe", "-"], timeout=120, check=False, text=False)
        data = p.stdout or b""
        if p.returncode != 0 or not data:
            continue
        new = shrink_png(data) if st.codec == "png" else shrink_jpeg(data)
        if len(new) < len(data):
            path = folder / f"cover{st.index}{'.png' if st.codec == 'png' else '.jpg'}"
            path.write_bytes(new)
            result.covers[st.index] = path
            result.pictures += 1
            result.saved += len(data) - len(new)
    return result if result.covers else None


def _pictures(src: Path, info: VideoInfo, folder: Path, result: FontPlan) -> FontPlan:
    """JPEG and PNG attachments with the same pixels in fewer bytes (where that works)."""
    from .documents import shrink_jpeg, shrink_png

    pictures = [st for st in info.streams if is_picture(st)]
    files = _dump(src, pictures, folder) if pictures else {}
    for st in pictures:
        path = files.get(st.index)
        if path is None:
            continue
        data = path.read_bytes()
        png = data[:8] == b"\x89PNG\r\n\x1a\n"
        new = shrink_png(data) if png else shrink_jpeg(data) if data[:2] == b"\xff\xd8" else data
        if len(new) >= len(data):
            continue
        smaller = folder / f"small{st.index}{path.suffix}"
        smaller.write_bytes(new)
        result.replace.append(st.index)
        result.attach.append((smaller, st.filename or path.name, "image/png" if png else "image/jpeg"))
        result.pictures += 1
        result.saved += len(data) - len(new)
    return result


def _fonts(src: Path, info: VideoInfo, subs: list[StreamInfo], folder: Path) -> FontPlan | None:
    from fontTools.ttLib import TTCollection, TTFont

    fonts = [st for st in info.streams if is_font(st)]
    if not fonts:
        return None
    used: dict[str, set[int]] = {}
    for st in subs:
        if st.codec in ASS_CODECS:
            for name, drawn in used_characters(_subtitle_text(src, st.index)).items():
                used.setdefault(name, set()).update(drawn)
    files = _dump(src, fonts, folder)
    if len(files) != len(fonts):
        return None                                      # (couldn't get every font out: leave them all)
    result = FontPlan()
    for st in fonts:
        path = files[st.index]
        name = st.filename or path.name
        try:
            if path.suffix in (".ttc", ".otc"):
                with TTCollection(path) as coll:
                    names = set().union(*(_names(f) for f in coll.fonts))
                cuttable = False
            else:
                with TTFont(path, lazy=True) as f:
                    names = _names(f)
                cuttable = True
        except Exception:  # noqa: BLE001 - not a font we can read: keep it as it is
            from .log import ignored
            ignored("subfonts.plan")
            result.attach.append((path, name, st.mimetype or "application/x-truetype-font"))
            continue
        if names and not names & set(used):
            result.dropped += 1
            result.saved += path.stat().st_size
            continue
        kept = path
        chars: set[int] = set().union(*(used[n] for n in names if n in used))
        if cuttable and chars:
            smaller = folder / f"cut{st.index}{path.suffix}"
            try:
                if subset(path, smaller, chars):
                    kept = smaller
                    result.cut += 1
                    result.saved += path.stat().st_size - smaller.stat().st_size
            except Exception:  # noqa: BLE001 - keep this one whole
                from .log import ignored
                ignored("subfonts.subset")
        result.attach.append((kept, name, st.mimetype or "application/x-truetype-font"))
    if not (result.cut or result.dropped):
        return None
    result.replace = [st.index for st in fonts]
    return result


def args(fonts: FontPlan, mapped_attachments: int) -> list[str]:
    """ffmpeg output arguments that attach the plan's fonts (after the attachments that are copied)."""
    out: list[str] = []
    for i, (path, name, mime) in enumerate(fonts.attach):
        n = mapped_attachments + i
        out += ["-attach", str(path), f"-metadata:s:t:{n}", f"mimetype={mime}", f"-metadata:s:t:{n}",
                f"filename={name}"]
    return out


__all__ = ["FontPlan", "plan", "args", "used_characters", "subset", "is_font", "is_picture", "font_bytes",
           "MIN_FONT_BYTES"]
