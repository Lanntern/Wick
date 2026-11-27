"""Office files and e-books, beyond shrinking their pictures: parts that take space without being shown.

* **Orphaned parts**: pictures and embedded files nothing in the document points to any more (Word and PowerPoint
  keep a picture after it's deleted or replaced). Found from the package's relationships (OOXML) or from every
  reference in its XML (OpenDocument); removed with their content-type or manifest entry.
* **BMP and TIFF pictures** inside become PNG: the same pixels, every reference renamed.
* **Cropped-away areas** (an option): a picture cropped in Word or PowerPoint is stored whole. When nothing else
  uses that picture, the hidden part is cut off and the crop removed, so it looks exactly the same.
* **Excel pivot caches** (an option): the cached copy of a pivot table's source data, for tables whose source is
  in the workbook; Excel rebuilds it when the file opens.
* **E-book fonts**: EPUB fonts (scrambled the IDPF or Adobe way, or plain) cut to the characters in the book.
"""

from __future__ import annotations

import hashlib
import html
import io
import posixpath
import re
import tempfile
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import unquote

from .formats import plural
from .log import ignored

_REL = re.compile(r"<Relationship\b[^>]*?/?>", re.S)
_ATTR = re.compile(r'([\w:]+)="([^"]*)"')
PICTURE_EXTS = (".png", ".jpg", ".jpeg")


@dataclass
class Package:
    """A zip package being rebuilt: its parts in order, what's removed, renamed and changed."""
    names: list[str]
    data: dict[str, bytes]
    removed: set[str] = field(default_factory=set)
    renamed: dict[str, str] = field(default_factory=dict)     # old name -> new name
    changed: set[str] = field(default_factory=set)
    cropped: set[str] = field(default_factory=set)            # pictures whose pixels changed on purpose
    notes: list[str] = field(default_factory=list)

    @property
    def ooxml(self) -> bool:
        return "[Content_Types].xml" in self.data

    @property
    def odf(self) -> bool:
        return "META-INF/manifest.xml" in self.data and self.data.get("mimetype", b"").startswith(
            b"application/vnd.oasis.opendocument")

    def set(self, name: str, value: bytes) -> None:
        if value != self.data[name]:
            self.data[name] = value
            self.changed.add(name)

    def text(self, name: str) -> str:
        return self.data[name].decode("utf-8", "replace")


# ---------------------------------------------------------------- relationships

def _attrs(tag: str) -> dict[str, str]:
    return {k: html.unescape(v) for k, v in _ATTR.findall(tag)}


def _rels(pkg: Package) -> dict[str, list[dict[str, str]]]:
    """{rels part: its relationships, each with "_target" resolved to a part name (or "" for external ones)}."""
    out = {}
    for name in pkg.names:
        if name in pkg.removed or not name.endswith(".rels") or "_rels/" not in name:
            continue
        folder = name.rsplit("_rels/", 1)[0]
        items = []
        for tag in _REL.findall(pkg.text(name)):
            a = _attrs(tag)
            target = unquote(a.get("Target", ""))
            if a.get("TargetMode") == "External" or not target:
                a["_target"] = ""
            elif target.startswith("/"):
                a["_target"] = target[1:]
            else:
                a["_target"] = posixpath.normpath(folder + target)
            items.append(a)
        out[name] = items
    return out


def _source_of(rels_name: str) -> str:
    folder, file = rels_name.rsplit("_rels/", 1)
    return folder + file[:-len(".rels")]


def _drop_override(pkg: Package, part: str) -> None:
    ct = pkg.text("[Content_Types].xml")
    new = re.sub(r'<Override\b[^>]*PartName="/' + re.escape(part) + r'"[^>]*/>', "", ct)
    pkg.set("[Content_Types].xml", new.encode("utf-8"))


# ---------------------------------------------------------------- orphans

def remove_orphans(pkg: Package) -> int:
    """Remove pictures and embedded files nothing points to. Returns how many."""
    removed = 0
    if pkg.ooxml:
        try:
            rels = _rels(pkg)
        except Exception:  # noqa: BLE001 - relationships we can't read: nothing is removed
            ignored("officeslim: relationships")
            return 0
        used = {r["_target"] for items in rels.values() for r in items}
        # Only in folders the relationships do point into: a package without them (an odd writer) keeps all.
        folders = {posixpath.dirname(t) for t in used}
        for name in pkg.names:
            low = name.lower()
            if name in pkg.removed or not ("/media/" in low or "/embeddings/" in low):
                continue
            if posixpath.dirname(name) in folders and name not in used:
                pkg.removed.add(name)
                _drop_override(pkg, name)
                removed += 1
    elif pkg.odf:
        texts = "".join(pkg.text(n) for n in pkg.names if n.endswith(".xml") and n != "META-INF/manifest.xml")
        for name in pkg.names:
            if not name.startswith("Pictures/") or name in pkg.removed or name in texts:
                continue
            pkg.removed.add(name)
            manifest = pkg.text("META-INF/manifest.xml")
            manifest = re.sub(r'<manifest:file-entry\b[^>]*manifest:full-path="' + re.escape(name) + r'"[^>]*/>',
                              "", manifest)
            pkg.set("META-INF/manifest.xml", manifest.encode("utf-8"))
            removed += 1
    if removed:
        pkg.notes.append(f"{plural(removed, 'unused picture')} or file{'s' if removed != 1 else ''}"
                         " removed")
    return removed


# ---------------------------------------------------------------- BMP and TIFF to PNG

def bitmaps_to_png(pkg: Package) -> int:
    """BMP and TIFF pictures in an Office file's media folders become PNG (same pixels). Returns how many."""
    from .documents import same_pixels, shrink_png

    if not pkg.ooxml:
        return 0
    try:
        rels = _rels(pkg)
    except Exception:  # noqa: BLE001
        ignored("officeslim: relationships")
        return 0
    done = 0
    for name in list(pkg.names):
        low = name.lower()
        if name in pkg.removed or "/media/" not in low or not low.endswith((".bmp", ".tif", ".tiff")):
            continue
        from PIL import Image

        try:
            with Image.open(io.BytesIO(pkg.data[name])) as im:
                if getattr(im, "n_frames", 1) > 1:
                    continue
                buf = io.BytesIO()
                im.save(buf, "PNG")
            png = shrink_png(buf.getvalue())
            if not same_pixels(pkg.data[name], png):
                continue
        except Exception:  # noqa: BLE001 - this picture stays as it is
            ignored("officeslim: picture to PNG")
            continue
        stem = name.rsplit(".", 1)[0]
        new = stem + ".png"
        n = 1
        while new in pkg.data:
            new, n = f"{stem}-{n}.png", n + 1
        ext = name[len(stem):]
        for rels_name, items in rels.items():
            text = pkg.text(rels_name)
            for r in items:
                if r["_target"] == name:
                    old_target = r["Target"]
                    new_target = old_target[: len(old_target) - len(ext)] + ".png"
                    text = text.replace(f'Target="{old_target}"', f'Target="{new_target}"')
            pkg.set(rels_name, text.encode("utf-8"))
        ct = pkg.text("[Content_Types].xml")
        if not re.search(r'<Default\b[^>]*Extension="png"', ct, re.I):
            ct = ct.replace("<Default ", '<Default Extension="png" ContentType="image/png"/><Default ', 1)
        pkg.set("[Content_Types].xml", ct.encode("utf-8"))
        _drop_override(pkg, name)
        pkg.data[new] = png
        pkg.renamed[name] = new
        pkg.changed.add(new)
        done += 1
    if done:
        pkg.notes.append(f"{plural(done, 'BMP or TIFF picture')} stored as PNG")
    return done


# ---------------------------------------------------------------- cropped areas

_BLIPFILL = re.compile(r"<(\w+):blipFill\b[^>]*>.*?</\1:blipFill>", re.S)
_EMBED = re.compile(r'<a:blip\b[^>]*?r:embed="([^"]+)"')
_SRCRECT = re.compile(r"<a:srcRect\b[^>]*/>")


def crop_pictures(pkg: Package, lossy_ok: bool) -> int:
    """Cut off what a crop hides, for pictures used once (PNG always; JPEG only when ``lossy_ok``, since cutting it
    means encoding it again). Returns how many."""
    if not pkg.ooxml:
        return 0
    try:
        rels = _rels(pkg)
    except Exception:  # noqa: BLE001
        ignored("officeslim: relationships")
        return 0
    uses: dict[str, int] = {}
    for items in rels.values():
        for r in items:
            uses[r["_target"]] = uses.get(r["_target"], 0) + 1
    blips = _picture_fills(pkg, {_source_of(n): {r.get("Id"): r["_target"] for r in items} for n, items in rels.items()})
    per_target: dict[str, int] = {}
    for *_, target in blips:
        per_target[target] = per_target.get(target, 0) + 1
    done = 0
    edits: dict[str, list[tuple[int, int, str]]] = {}
    for part, start, block, target in blips:
        rect = _SRCRECT.search(block)
        if not rect or not target or uses.get(target) != 1 or per_target.get(target) != 1:
            continue
        a = {k: int(v) for k, v in _ATTR.findall(rect.group(0)) if k in ("l", "t", "r", "b") and v.lstrip("-").isdigit()}
        name = pkg.renamed.get(target, target)
        if not a or any(v < 0 for v in a.values()) or not any(a.values()) or name not in pkg.data \
                or not name.lower().endswith(PICTURE_EXTS):
            continue
        jpeg = not name.lower().endswith(".png")
        new = _cropped(pkg.data[name], a, jpeg) if lossy_ok or not jpeg else None
        if new is None or len(new) >= len(pkg.data[name]):
            continue
        pkg.data[name] = new
        pkg.changed.add(name)
        pkg.cropped.add(name)
        edits.setdefault(part, []).append((start + rect.start(), start + rect.end(), "<a:srcRect/>"))
        done += 1
    for part, changes in edits.items():
        text = pkg.text(part)
        for s, e, rep in sorted(changes, reverse=True):
            text = text[:s] + rep + text[e:]
        pkg.set(part, text.encode("utf-8"))
    if done:
        pkg.notes.append(f"cropped-away parts of {plural(done, 'picture')} removed")
    return done


def _picture_fills(pkg: Package, by_part: dict[str, dict]) -> list[tuple[str, int, str, str]]:
    """Every picture fill in the document's parts: (part, where it starts, its XML, the picture it shows)."""
    out = []
    for part in pkg.names:
        if part in pkg.removed or not part.endswith(".xml") or part not in by_part:
            continue
        for m in _BLIPFILL.finditer(pkg.text(part)):
            embed = _EMBED.search(m.group(0))
            if embed:
                out.append((part, m.start(), m.group(0), by_part[part].get(embed.group(1), "")))
    return out


def _cropped(data: bytes, crop: dict[str, int], jpeg: bool) -> bytes | None:
    """The picture with the cropped-away edges (in 1/100,000ths of a side) cut off; None if it can't be."""
    from PIL import Image

    from .documents import shrink_png

    try:
        with Image.open(io.BytesIO(data)) as im:
            w, h = im.size
            box = (round(w * crop.get("l", 0) / 100000), round(h * crop.get("t", 0) / 100000),
                   w - round(w * crop.get("r", 0) / 100000), h - round(h * crop.get("b", 0) / 100000))
            if box[2] - box[0] < 1 or box[3] - box[1] < 1:
                return None
            part_img = im.crop(box)
            buf = io.BytesIO()
            if jpeg:
                part_img.convert("RGB" if part_img.mode not in ("RGB", "L", "CMYK") else part_img.mode).save(
                    buf, "JPEG", quality=90, optimize=True, icc_profile=im.info.get("icc_profile"))
                return buf.getvalue()
            part_img.save(buf, "PNG", icc_profile=im.info.get("icc_profile"))
            return shrink_png(buf.getvalue())
    except Exception:  # noqa: BLE001 - a picture we can't read or write stays as it is
        ignored("officeslim: cropping a picture")
        return None


# ---------------------------------------------------------------- Excel pivot caches

def drop_pivot_caches(pkg: Package) -> int:
    """For pivot tables whose source is a range in the workbook: remove the cached records and have Excel rebuild
    them on open. Returns how many caches."""
    done = 0
    for name in list(pkg.names):
        if not re.fullmatch(r"xl/pivotCache/pivotCacheDefinition\d+\.xml", name) or name in pkg.removed:
            continue
        text = pkg.text(name)
        if '<cacheSource type="worksheet"' not in text:
            continue
        rels_name = f"xl/pivotCache/_rels/{name.rsplit('/', 1)[1]}.rels"
        if rels_name not in pkg.data:
            continue
        rels = pkg.text(rels_name)
        rec = next((_attrs(t) for t in _REL.findall(rels) if _attrs(t).get("Type", "").endswith("/pivotCacheRecords")),
                   None)
        if rec is None:
            continue
        records = posixpath.normpath("xl/pivotCache/" + rec["Target"]) if not rec["Target"].startswith("/") \
            else rec["Target"][1:]
        if records not in pkg.data:
            continue
        root = re.search(r"<pivotCacheDefinition\b[^>]*>", text)
        if root is None:
            continue
        tag = re.sub(r'\s+r:id="[^"]*"', "", root.group(0))
        tag = re.sub(r'\s+refreshOnLoad="[^"]*"', "", tag)
        tag = tag[:-1].rstrip("/") + ' refreshOnLoad="1"' + (">" if not tag.endswith("/>") else "/>")
        pkg.set(name, (text[:root.start()] + tag + text[root.end():]).encode("utf-8"))
        new_rels = re.sub(r'<Relationship\b[^>]*Id="' + re.escape(rec["Id"]) + r'"[^>]*/>', "", rels)
        pkg.set(rels_name, new_rels.encode("utf-8"))
        pkg.removed.add(records)
        records_rels = f"xl/pivotCache/_rels/{records.rsplit('/', 1)[1]}.rels"
        if records_rels in pkg.data:
            pkg.removed.add(records_rels)
        _drop_override(pkg, records)
        done += 1
    if done:
        pkg.notes.append(f"{plural(done, 'pivot table cache')} left for Excel to rebuild")
    return done


# ---------------------------------------------------------------- EPUB fonts

class Protected(ValueError):
    """An e-book with copy protection (DRM): left alone."""


def _epub_identifier(pkg: Package) -> str:
    container = pkg.text("META-INF/container.xml") if "META-INF/container.xml" in pkg.data else ""
    m = re.search(r'full-path="([^"]+)"', container)
    opf = pkg.text(unquote(m.group(1))) if m and unquote(m.group(1)) in pkg.data else ""
    uid = re.search(r'unique-identifier="([^"]+)"', opf)
    if not uid:
        return ""
    ident = re.search(r'<dc:identifier\b[^>]*\bid="' + re.escape(uid.group(1)) + r'"[^>]*>(.*?)</dc:identifier>',
                      opf, re.S)
    return html.unescape(ident.group(1)).strip() if ident else ""


def _xor(data: bytes, key: bytes, length: int) -> bytes:
    return bytes(b ^ key[i % len(key)] for i, b in enumerate(data[:length])) + data[length:]


IDPF = "http://www.idpf.org/2008/embedding"
ADOBE = "http://ns.adobe.com/pdf/enc#RC"


def epub_fonts(pkg: Package) -> int:
    """Cut an e-book's fonts to the characters in its text. Returns how many."""

    from .subfonts import BASE, subset

    if "META-INF/rights.xml" in pkg.data:
        raise Protected("Copy-protected e-book")
    scrambled: dict[str, str] = {}
    if "META-INF/encryption.xml" in pkg.data:
        enc = pkg.text("META-INF/encryption.xml")
        for block in re.findall(r"<(?:\w+:)?EncryptedData\b.*?</(?:\w+:)?EncryptedData>", enc, re.S):
            method = re.search(r'Algorithm="([^"]+)"', block)
            uri = re.search(r'URI="([^"]+)"', block)
            if not method or not uri:
                continue
            if method.group(1) not in (IDPF, ADOBE):
                raise Protected("Copy-protected e-book")
            scrambled[unquote(uri.group(1))] = method.group(1)
    ident = _epub_identifier(pkg)
    keys = {IDPF: hashlib.sha1(re.sub(r"[\x20\x09\x0d\x0a]", "", ident).encode()).digest()}
    try:
        keys[ADOBE] = uuid.UUID(ident.split(":")[-1]).bytes
    except ValueError:
        pass
    lengths = {IDPF: 1040, ADOBE: 1024}
    text = "".join(html.unescape(re.sub(r"<[^>]+>", " ", pkg.text(n))) for n in pkg.names
                   if n.lower().endswith((".xhtml", ".html", ".htm", ".xml", ".ncx", ".opf", ".css")))
    chars = set(BASE) | {ord(c) for c in text}
    done = 0
    with tempfile.TemporaryDirectory(prefix="wick-epubfont-") as folder:
        for name in pkg.names:
            if name in pkg.removed or not name.lower().endswith((".ttf", ".otf")):
                continue
            algo = scrambled.get(name)
            if algo is not None and algo not in keys:
                continue
            data = pkg.data[name]
            plain = _xor(data, keys[algo], lengths[algo]) if algo else data
            src, dst = Path(folder) / "in.font", Path(folder) / "out.font"
            src.write_bytes(plain)
            try:
                if not subset(src, dst, chars):
                    continue
            except Exception:  # noqa: BLE001 - not a font we can read (or not this key): it stays as it is
                ignored("officeslim: cutting a font")
                continue
            new = dst.read_bytes()
            pkg.set(name, _xor(new, keys[algo], lengths[algo]) if algo else new)
            done += 1
    if done:
        pkg.notes.append(f"{plural(done, 'font')} cut to the characters in the book")
    return done


__all__ = ["Package", "remove_orphans", "bitmaps_to_png", "crop_pictures", "drop_pivot_caches", "epub_fonts",
           "Protected", "PICTURE_EXTS"]
