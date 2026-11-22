"""Fonts embedded in documents, cut down to what the document uses.

Office (Word, PowerPoint, OpenDocument): an embedded font often holds thousands of characters for a document
that uses a few hundred. Each font is cut to every character that appears anywhere in the document's text (plus
ASCII, Latin-1 and common punctuation). Word scrambles the start of its embedded fonts with a key kept in the
font table; the font is unscrambled, cut and scrambled again with the same key, and Word is told it's now a
subset. A font that can't be read, or whose licence forbids subsetting, is left as it is.

PDF: fonts embedded whole (no "ABCDEF+" subset tag), as TrueType, are cut to the glyphs the pages draw with them.
Glyph numbers stay the same, so every character still points at the same glyph. Every page is drawn before and
after; if a single pixel differs, the fonts are put back as they were.
"""

from __future__ import annotations

import hashlib
import html
import re
from pathlib import Path

from .subfonts import BASE

# ---------------------------------------------------------------- Office

FONT_PARTS = ("word/fonts/", "ppt/fonts/", "fonts/")
_KEY = re.compile(rb'w:fontKey="\{([0-9A-Fa-f-]{36})\}"')
_SYM = re.compile(rb'w:char="([0-9A-Fa-f]{2,4})"')
_TAG = re.compile(r"<[^>]+>")


def is_font_part(name: str) -> bool:
    low = name.lower()
    return low.startswith(FONT_PARTS) and low.rsplit(".", 1)[-1] in ("odttf", "ttf", "otf", "fntdata")


def office_text(parts: dict[str, bytes]) -> set[int]:
    """Every character the document's XML holds as text (more than is shown: harmless), and Word's symbols."""
    chars: set[int] = set(BASE)
    for name, data in parts.items():
        if not name.lower().endswith(".xml"):
            continue
        text = data.decode("utf-8", "replace")
        chars.update(ord(c) for c in html.unescape(_TAG.sub(" ", text)))
        for m in _SYM.finditer(data):
            code = int(m.group(1), 16)
            chars.update((code, code & 0xFF, 0xF000 | (code & 0xFF)))
    return chars


def office_keys(parts: dict[str, bytes]) -> list[bytes]:
    """Word's font keys (each a GUID), from the font table."""
    return [bytes.fromhex(m.group(1).decode().replace("-", "")) for m in _KEY.finditer(parts.get("word/fontTable.xml",
                                                                                              b""))]


def _xor(data: bytes, key: bytes) -> bytes:
    head = bytes(b ^ key[i % 16] for i, b in enumerate(data[:32]))
    return head + data[32:]


def _readable(data: bytes) -> bool:
    from io import BytesIO

    from fontTools.ttLib import TTFont

    try:
        with TTFont(BytesIO(data), lazy=True) as f:
            return "glyf" in f or "CFF " in f
    except Exception:  # noqa: BLE001
        return False


def cut_office_font(data: bytes, chars: set[int], keys: list[bytes]) -> tuple[bytes, bytes | None] | None:
    """(the font cut and scrambled again as it was, the font table's key it was scrambled with or None), or None
    when it can't or needn't be cut."""
    import tempfile

    from .subfonts import subset

    plain: bytes | None = data if _readable(data) else None
    key: bytes | None = None
    table_key: bytes | None = None
    if plain is None:
        for k in keys:
            for candidate in (k[::-1], k):                 # (the GUID's bytes, reversed: the usual order)
                if _readable(_xor(data, candidate)):
                    plain, key, table_key = _xor(data, candidate), candidate, k
                    break
            if plain is not None:
                break
    if plain is None:
        return None
    with tempfile.TemporaryDirectory(prefix="wick-docfont-") as folder:
        src, dst = Path(folder) / "in.ttf", Path(folder) / "out.ttf"
        src.write_bytes(plain)
        if not subset(src, dst, chars):
            return None
        new = dst.read_bytes()
    return (_xor(new, key) if key is not None else new), table_key


def mark_subsetted(font_table: bytes, keys: list[bytes]) -> bytes:
    """Word's font table, with the cut fonts marked as subsets (so Word doesn't expect every character)."""
    for key in keys:
        hexed = key.hex().upper()
        text = f"{hexed[:8]}-{hexed[8:12]}-{hexed[12:16]}-{hexed[16:20]}-{hexed[20:]}"
        pattern = re.compile(rb'(<w:embed\w+\b[^>]*w:fontKey="\{' + text.encode() + rb'\}"[^>]*?)(\s*/?>)', re.I)

        def fix(m: re.Match) -> bytes:
            tag = re.sub(rb'\sw:subsetted="[^"]*"', b"", m.group(1))
            return tag + b' w:subsetted="1"' + m.group(2)

        font_table = pattern.sub(fix, font_table)
    return font_table


# ---------------------------------------------------------------- PDF

MAX_PAGES_TO_CHECK = 300
_SUBSET_TAG = re.compile(r"^[A-Z]{6}\+")


def _font_program(font):
    """(the dict holding the descriptor, the TrueType stream, 'cid' or 'simple') for a font embedded whole."""
    import pikepdf

    sub = font.get("/Subtype")
    if sub == "/Type0":
        if str(font.get("/Encoding")) not in ("/Identity-H", "/Identity-V"):
            return None
        kids = font.get("/DescendantFonts")
        if not isinstance(kids, pikepdf.Array) or len(kids) != 1:
            return None
        cid = kids[0]
        if cid.get("/Subtype") not in ("/CIDFontType2", "/CIDFontType0"):
            return None
        if cid.get("/Subtype") == "/CIDFontType2" and str(cid.get("/CIDToGIDMap", "/Identity")) != "/Identity":
            return None
        holder, kind = cid, "cid"
    elif sub in ("/TrueType", "/Type1", "/MMType1"):
        holder, kind = font, "simple"
    else:
        return None
    name = str(holder.get("/BaseFont", ""))[1:]
    desc = holder.get("/FontDescriptor")
    if _SUBSET_TAG.match(name) or desc is None:
        return None
    if "/FontFile2" in desc:
        return holder, desc.FontFile2, kind
    ff3 = desc.get("/FontFile3")
    if ff3 is not None and ff3.get("/Subtype") == "/OpenType":       # (a whole OpenType font, CFF or TrueType)
        return holder, ff3, kind
    return None


def _walk(pdf):
    """(font dict, bytes it shows) for every text-showing operator in pages, forms and annotation appearances."""
    import pikepdf

    seen: set = set()

    def resources_fonts(res):
        fonts = res.get("/Font") if isinstance(res, pikepdf.Dictionary) else None
        return dict(fonts.items()) if isinstance(fonts, pikepdf.Dictionary) else {}

    def stream(content, res):
        obj = content.obj if isinstance(content, pikepdf.Page) else content
        key = obj.objgen if obj.is_indirect else id(obj)
        if key in seen:
            return
        seen.add(key)
        fonts = resources_fonts(res)
        xobjects = res.get("/XObject") if isinstance(res, pikepdf.Dictionary) else None
        current = None
        for operands, op in pikepdf.parse_content_stream(content):
            op = str(op)
            if op == "Tf" and operands:
                current = fonts.get(str(operands[0]))
            elif op in ("Tj", "'", '"') and operands and current is not None:
                yield current, bytes(operands[-1])
            elif op == "TJ" and operands and current is not None:
                for part in operands[0]:
                    if isinstance(part, pikepdf.String):
                        yield current, bytes(part)
            elif op == "Do" and operands and isinstance(xobjects, pikepdf.Dictionary):
                x = xobjects.get(str(operands[0]))
                if isinstance(x, pikepdf.Stream) and x.get("/Subtype") == "/Form":
                    yield from stream(x, x.get("/Resources", res))

    for page in pdf.pages:
        res = page.obj.get("/Resources", pikepdf.Dictionary())
        yield from stream(page, res)
        for annot in page.obj.get("/Annots", []):
            ap = annot.get("/AP") if isinstance(annot, pikepdf.Dictionary) else None
            normal = ap.get("/N") if isinstance(ap, pikepdf.Dictionary) else None
            for s in ([normal] if isinstance(normal, pikepdf.Stream)
                      else list(normal.values()) if isinstance(normal, pikepdf.Dictionary) else []):
                if isinstance(s, pikepdf.Stream):
                    yield from stream(s, s.get("/Resources", pikepdf.Dictionary()))


def _differences(font) -> dict[int, str]:
    """A simple font's own code → glyph name changes (its encoding's /Differences)."""
    import pikepdf

    enc = font.get("/Encoding")
    diffs = enc.get("/Differences") if isinstance(enc, pikepdf.Dictionary) else None
    out: dict[int, str] = {}
    code = 0
    for item in diffs or []:
        if isinstance(item, pikepdf.Name):
            out[code] = str(item)[1:]
            code += 1
        else:
            code = int(item)
    return out


def _simple_glyphs(tt, codes: set[int], font=None) -> set[str]:
    """Glyph names a simple font may show for these one-byte codes (every reading a viewer might use: the font's
    character maps, its encoding's differences, and the standard glyph names)."""
    from fontTools.agl import UV2AGL

    order = tt.getGlyphOrder()
    known = set(order)
    diffs = _differences(font) if font is not None else {}
    names = {".notdef"}
    tables = {(t.platformID, t.platEncID): t.cmap for t in tt["cmap"].tables} if "cmap" in tt else {}
    for code in codes:
        for table, value in (((3, 0), code), ((3, 0), 0xF000 + code), ((1, 0), code), ((3, 1), code)):
            g = tables.get(table, {}).get(value)
            if g:
                names.add(g)
        try:
            u = ord(bytes([code]).decode("cp1252"))
        except UnicodeDecodeError:
            continue
        g = tables.get((3, 1), {}).get(u)
        if g:
            names.add(g)
        if code < len(order):
            names.add(order[code])
        for name in (diffs.get(code), UV2AGL.get(u)):
            if name is not None and name in known:
                names.add(name)
    return names


def cut_pdf_fonts(pdf) -> int:
    """Cut the fonts embedded whole in an open PDF (pikepdf) to the glyphs drawn. Returns the bytes saved."""
    from io import BytesIO

    from fontTools import subset as ft_subset
    from fontTools.ttLib import TTFont

    from .subfonts import _quiet_fonttools

    _quiet_fonttools()
    if pdf.Root.get("/AcroForm") is not None:
        return 0                   # (form fields draw their text when they're filled in: any character)
    used: dict = {}
    for font, data in _walk(pdf):
        if font.is_indirect:
            used.setdefault(font.objgen, (font, set()))[1].add(data)     # (whole strings: CID codes are pairs)
    saved = 0
    for font, raw in used.values():
        found = _font_program(font)
        if found is None:
            continue
        holder, program, kind = found
        data = program.read_bytes()
        try:
            tt = TTFont(BytesIO(data))
            if "OS/2" in tt and tt["OS/2"].fsType & 0x0302:
                continue
            if kind == "cid":
                cids = {int.from_bytes(text[i:i + 2], "big") for text in raw for i in range(0, len(text) - 1, 2)} | {0}
                order = tt.getGlyphOrder()
                cff = tt["CFF "].cff.topDictIndex[0] if "CFF " in tt else None
                if cff is not None and hasattr(cff, "ROS"):           # CID-keyed CFF: glyphs are named by CID
                    known = set(order)
                    glyphs = {f"cid{c:05d}" for c in cids if f"cid{c:05d}" in known} | {order[0]}
                else:
                    glyphs = {order[g] for g in cids if g < len(order)}
            else:
                glyphs = _simple_glyphs(tt, {b for text in raw for b in text}, font)
            opts = ft_subset.Options()
            opts.retain_gids = True
            opts.notdef_outline = True
            opts.name_IDs = ["*"]
            opts.name_languages = ["*"]
            opts.layout_features = []
            opts.symbol_cmap = True
            opts.legacy_cmap = True
            sub = ft_subset.Subsetter(opts)
            sub.populate(glyphs=sorted(glyphs))
            sub.subset(tt)
            out = BytesIO()
            tt.save(out)
            new = out.getvalue()
        except Exception:  # noqa: BLE001 - this font stays whole
            from .log import ignored
            ignored("docfonts.cut_pdf_fonts")
            continue
        if len(new) >= len(data) * 0.9:
            continue
        program.write(new)
        if "/Length1" in program:
            program.Length1 = len(new)
        _tag(font, holder, data)
        saved += len(data) - len(new)
    return saved


def _tag(font, holder, data: bytes) -> None:
    """The subset tag the PDF standard asks for ("ABCDEF+Name"), on every name of the font."""
    import pikepdf

    tag = "".join(chr(65 + b % 26) for b in hashlib.sha1(data).digest()[:6]) + "+"
    name = str(holder.BaseFont)[1:]
    holder.BaseFont = pikepdf.Name("/" + tag + name)
    holder.FontDescriptor.FontName = pikepdf.Name("/" + tag + name)
    if holder is not font:
        font.BaseFont = pikepdf.Name("/" + tag + str(font.BaseFont)[1:])


def same_pages(a: Path, b: Path, dpi: int = 72) -> bool:
    """Do both PDFs draw every page with exactly the same pixels?"""
    from PySide6.QtCore import QSize
    from PySide6.QtPdf import QPdfDocument, QPdfDocumentRenderOptions

    options = QPdfDocumentRenderOptions()
    options.setRenderFlags(QPdfDocumentRenderOptions.RenderFlag.Annotations)     # (form and comment appearances too)
    docs = [QPdfDocument(), QPdfDocument()]
    try:
        for d, p in zip(docs, (a, b), strict=True):
            if d.load(str(p)) != QPdfDocument.Error.None_:
                return False
        if docs[0].pageCount() != docs[1].pageCount():
            return False
        for i in range(docs[0].pageCount()):
            pts = docs[0].pagePointSize(i)
            size = QSize(max(1, round(pts.width() * dpi / 72)), max(1, round(pts.height() * dpi / 72)))
            one, two = (d.render(i, size, options) for d in docs)
            if one.isNull() or two.isNull() or one != two:
                return False
        return True
    finally:
        for d in docs:
            d.close()


__all__ = ["is_font_part", "office_text", "office_keys", "cut_office_font", "mark_subsetted", "cut_pdf_fonts",
           "same_pages", "MAX_PAGES_TO_CHECK"]
