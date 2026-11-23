"""PDFs, beyond pictures and fonts: what takes space without being shown.

* **Page thumbnails** (``/Thumb``): small pictures of each page from early PDF versions; viewers draw their own.
* **Editing data** (``/PieceInfo``): Illustrator ("Preserve Illustrator Editing Capabilities") and Photoshop keep
  a whole copy of their own file inside the PDF. Leaving it out changes nothing about how the PDF looks, but
  Illustrator can then only open it as a plain PDF. An option, off by default.
* **Identical streams** stored more than once (a logo or font on every page of merged PDFs): every reference is
  pointed at one copy.
* **Black-and-white scans** stored with general-purpose compression become CCITT Group 4, the fax standard made
  for them: the same pixels, often a half to a third of the size.

The last two change the file's structure, so the caller draws every page before and after (docfonts.same_pages).
"""

from __future__ import annotations

import hashlib
import io


def drop_thumbnails(pdf) -> int:
    """Remove every page's thumbnail. Returns how many there were."""
    n = 0
    for page in pdf.pages:
        if "/Thumb" in page.obj:
            del page.obj["/Thumb"]
            n += 1
    return n


def drop_editing_data(pdf) -> int:
    """Remove Illustrator's and Photoshop's private editing data (``/PieceInfo`` on pages and the document)."""
    n = 0
    for holder in [pdf.Root] + [p.obj for p in pdf.pages]:
        if "/PieceInfo" in holder:
            del holder["/PieceInfo"]
            n += 1
    return n


def _key(stream) -> tuple[str, str]:
    import pikepdf

    items = sorted((k, repr(v)) for k, v in stream.items() if k != "/Length" and not isinstance(v, pikepdf.Stream))
    return repr(items), hashlib.sha256(stream.read_raw_bytes()).hexdigest()


def dedupe_streams(pdf) -> int:
    """Point every reference to a stream at one copy of each identical stream (same dictionary, same bytes).
    Returns how many copies were let go (saving them drops them)."""
    import pikepdf

    first: dict = {}
    replace: dict = {}
    for obj in pdf.objects:
        if not isinstance(obj, pikepdf.Stream) or not obj.is_indirect:
            continue
        if obj.get("/Type") in ("/XRef", "/ObjStm", "/Metadata"):
            continue
        try:
            key = _key(obj)
        except Exception:  # noqa: BLE001 - a stream that can't be read is left alone
            continue
        if key in first:
            replace[obj.objgen] = first[key]
        else:
            first[key] = obj
    if not replace:
        return 0

    def fix(container, depth: int = 0) -> None:
        if depth > 32:
            return
        if isinstance(container, (pikepdf.Dictionary, pikepdf.Stream)):
            for k in list(container.keys()):
                v = container[k]
                if not isinstance(v, pikepdf.Object):
                    continue                    # (numbers and booleans come back as Python values)
                if v.is_indirect and v.objgen in replace:
                    container[k] = replace[v.objgen]
                elif not v.is_indirect and isinstance(v, (pikepdf.Dictionary, pikepdf.Array)):
                    fix(v, depth + 1)
        elif isinstance(container, pikepdf.Array):
            for i in range(len(container)):
                v = container[i]
                if not isinstance(v, pikepdf.Object):
                    continue
                if v.is_indirect and v.objgen in replace:
                    container[i] = replace[v.objgen]
                elif not v.is_indirect and isinstance(v, (pikepdf.Dictionary, pikepdf.Array)):
                    fix(v, depth + 1)

    for obj in list(pdf.objects):
        if isinstance(obj, (pikepdf.Dictionary, pikepdf.Stream, pikepdf.Array)):
            fix(obj)
    fix(pdf.trailer)
    return len(replace)


def _group4(img) -> bytes | None:
    """Group 4 data for a 1-bit picture (one strip, as a PDF needs), or None."""
    from PIL import Image

    buf = io.BytesIO()
    img.save(buf, "TIFF", compression="group4", tiffinfo={278: img.height})
    with Image.open(io.BytesIO(buf.getvalue())) as tif:
        offsets, counts = tif.tag_v2.get(273), tif.tag_v2.get(279)
    if offsets is None or counts is None or len(offsets) != 1:
        return None
    return buf.getvalue()[offsets[0]:offsets[0] + counts[0]]


def scans_to_group4(pdf) -> int:
    """Black-and-white pictures (1 bit per pixel, not already fax- or JBIG2-coded) stored as Group 4, when that's
    at least 10% smaller and decodes to the same pixels. Returns the bytes saved."""
    import pikepdf
    from pikepdf import PdfImage

    saved = 0
    for obj in pdf.objects:
        if not isinstance(obj, pikepdf.Stream) or obj.get("/Subtype") != "/Image":
            continue
        if int(obj.get("/BitsPerComponent", 1 if obj.get("/ImageMask") else 0)) != 1:
            continue
        filters = obj.get("/Filter")
        names = [str(f) for f in filters] if isinstance(filters, pikepdf.Array) else [str(filters)] if filters else []
        if any(f in ("/CCITTFaxDecode", "/JBIG2Decode", "/DCTDecode", "/JPXDecode") for f in names):
            continue
        if obj.get("/ColorSpace") not in (None, "/DeviceGray") or "/SMask" in obj:
            continue
        original_raw = obj.read_raw_bytes()
        original_filter, original_parms = obj.get("/Filter"), obj.get("/DecodeParms")
        try:
            before = PdfImage(obj).as_pil_image().convert("1")
            data = _group4(before)
            if data is None or len(data) > len(original_raw) * 0.9:
                continue
            w, h = before.size
            for black_is_1 in (True, False):
                obj.write(data, filter=pikepdf.Name.CCITTFaxDecode,
                          decode_parms=pikepdf.Dictionary(K=-1, Columns=w, Rows=h, BlackIs1=black_is_1))
                if PdfImage(obj).as_pil_image().convert("1").tobytes() == before.tobytes():
                    saved += len(original_raw) - len(data)
                    break
            else:
                raise ValueError("Group 4 didn't give the same pixels")
        except Exception:  # noqa: BLE001 - this picture stays exactly as it was
            from .log import ignored
            ignored("pdfslim.scans_to_group4")
            if obj.read_raw_bytes() != original_raw:
                obj.write(original_raw, filter=original_filter, decode_parms=original_parms)  # type: ignore[arg-type]
            continue
    return saved


__all__ = ["drop_thumbnails", "drop_editing_data", "dedupe_streams", "scans_to_group4"]
