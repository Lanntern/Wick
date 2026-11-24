"""Office documents and PDFs: smaller without changing what they show.

Office files (.docx/.pptx/.xlsx and OpenDocument) are zip archives; nearly all of their size is pictures.
Each picture is shrunk losslessly (PNG optimized, JPEG optimized without re-encoding: identical pixels)
and the archive is recompressed. Everything else inside stays byte-for-byte the same, and that is checked.

PDFs: the same lossless JPEG optimization for pictures stored as JPEG, then a lossless restructure with
qpdf (compressed object streams, streams recompressed at the highest level). qpdf's check must pass.

"Reduce large pictures" (off by default) also scales pictures above a size down and re-encodes them.

Never changed: digitally signed documents (any change breaks the signature) and password-protected ones.
"""

from __future__ import annotations

import importlib.util
import io
import zipfile
from pathlib import Path

from .fileops import remove_quietly, temp_name
from .images import NotWorthIt, VerificationFailed

OFFICE_EXTS = {".docx", ".docm", ".dotx", ".pptx", ".pptm", ".potx", ".ppsx", ".xlsx", ".xlsm", ".xltx",
               ".odt", ".ods", ".odp", ".odg", ".epub", ".cbz"}
PDF_EXTS = {".pdf"}
_PICTURE_DIRS = ("media/", "pictures/", "embeddings/")    # word/media, ppt/media, xl/media; ODF Pictures/
_SIGNATURES = ("_xmlsignatures/", "meta-inf/documentsignatures.xml")
REDUCE_LONG_EDGE = 2560


class Unsupported(NotWorthIt):
    """A document this module leaves alone (signed, encrypted, not really an Office file)."""


def pdf_available() -> bool:

    return importlib.util.find_spec("pikepdf") is not None


# ---------------------------------------------------------------- pictures

def same_pixels(a: bytes, b: bytes) -> bool:
    from PIL import Image, ImageChops

    with Image.open(io.BytesIO(a)) as ia, Image.open(io.BytesIO(b)) as ib:
        if ia.size != ib.size:
            return False
        mode = "RGBA" if "A" in ia.getbands() or "A" in ib.getbands() or ia.mode == "P" else "RGB"
        if ia.mode in ("L", "1") and ib.mode in ("L", "1"):
            mode = "L"
        if ia.mode == "CMYK" or ib.mode == "CMYK":
            mode = "CMYK"
        return ImageChops.difference(ia.convert(mode), ib.convert(mode)).getbbox() is None


def shrink_jpeg(data: bytes) -> bytes:
    """Same pixels, fewer bytes (Huffman tables optimized, progressive): or the original."""
    try:
        import mozjpeg_lossless_optimization as mlo

        out = mlo.optimize(data)
    except Exception:  # noqa: BLE001 - odd JPEGs (CMYK, arithmetic...) stay as they are
        return data
    return out if len(out) < len(data) and same_pixels(data, out) else data


def shrink_png(data: bytes) -> bytes:
    try:
        import oxipng

        out = oxipng.optimize_from_memory(data, level=3)
    except Exception:  # noqa: BLE001
        return data
    return out if len(out) < len(data) and same_pixels(data, out) else data


def reduce_picture(data: bytes, ext: str, long_edge: int) -> bytes:
    """Scale a picture larger than ``long_edge`` down (and re-encode); else the original."""
    from PIL import Image

    try:
        with Image.open(io.BytesIO(data)) as im:
            w, h = im.size
            if max(w, h) <= long_edge:
                return data
            im.load()
            scale = long_edge / max(w, h)
            small = im.resize((max(1, round(w * scale)), max(1, round(h * scale))), Image.Resampling.LANCZOS)
            buf = io.BytesIO()
            if ext in (".jpg", ".jpeg"):
                if small.mode not in ("RGB", "L", "CMYK"):
                    small = small.convert("RGB")
                small.save(buf, "JPEG", quality=88, optimize=True, progressive=True,
                           icc_profile=im.info.get("icc_profile"))
            else:
                small.save(buf, "PNG", optimize=True)
                return min(shrink_png(buf.getvalue()), data, key=len)
            out = buf.getvalue()
    except Exception:  # noqa: BLE001
        return data
    return out if len(out) < len(data) else data


# ---------------------------------------------------------------- Office documents

def _is_picture(name: str) -> bool:
    low = name.lower()
    return any(d in low for d in _PICTURE_DIRS) and low.rsplit(".", 1)[-1] in ("png", "jpg", "jpeg")


def office_extras(path: Path) -> int:
    """Bytes in an Office file or e-book that more than picture shrinking can work on: embedded fonts, and BMP
    and TIFF pictures (from the directory only)."""
    from .docfonts import is_font_part

    with zipfile.ZipFile(path) as z:
        return sum(i.compress_size for i in z.infolist()
                   if is_font_part(i.filename) or i.filename.lower().endswith((".bmp", ".tif", ".tiff", ".ttf", ".otf")))


def office_pictures(path: Path) -> tuple[int, int, int]:
    """(JPEG bytes, PNG bytes, everything else) inside an Office file or e-book, from its directory only."""
    try:
        with zipfile.ZipFile(path) as z:
            infos = z.infolist()
    except (zipfile.BadZipFile, OSError) as e:
        raise Unsupported("Password-protected, or not a standard Office document") from e
    if any(i.filename.lower().startswith(_SIGNATURES) for i in infos):
        raise Unsupported("Digitally signed: any change would break the signature")
    book = path.suffix.lower() in (".epub", ".cbz", ".zip")
    jpeg = png = other = 0
    for i in infos:
        if _picture_part(i.filename, book):
            if i.filename.lower().endswith(".png"):
                png += i.compress_size
            else:
                jpeg += i.compress_size
        else:
            other += i.compress_size
    return jpeg, png, other


def _office_fonts(pkg) -> int:
    """Cut an Office file's embedded fonts to the document's characters (and mark them as subsets in Word's font
    table). Returns how many."""
    from . import docfonts

    fonts = [n for n in pkg.names if docfonts.is_font_part(n) and n not in pkg.removed]
    if not fonts:
        return 0
    xml = {n: pkg.data[n] for n in pkg.names if n.lower().endswith(".xml") and n not in pkg.removed}
    chars, keys = docfonts.office_text(xml), docfonts.office_keys(xml)
    used_keys, done = [], 0
    for name in fonts:
        try:
            cut = docfonts.cut_office_font(pkg.data[name], chars, keys)
        except Exception:  # noqa: BLE001 - this font stays as it is
            from .log import ignored
            ignored("documents._office_fonts")
            continue
        if cut is not None:
            pkg.set(name, cut[0])
            done += 1
            if cut[1] is not None:
                used_keys.append(cut[1])
    if used_keys and "word/fontTable.xml" in pkg.data:
        pkg.set("word/fontTable.xml", docfonts.mark_subsetted(pkg.data["word/fontTable.xml"], used_keys))
    return done


class _Zopfli:
    """A Deflate compressor for zipfile that gathers the data and compresses it with Zopfli at the end."""

    def __init__(self) -> None:
        self.parts: list[bytes] = []

    def compress(self, data: bytes) -> bytes:
        self.parts.append(bytes(data))
        return b""

    def flush(self) -> bytes:
        from zopfli import zopfli

        return zopfli.compress(b"".join(self.parts), numiterations=8)[2:-4]   # (zlib's header and checksum off)


_zopfli_here = __import__("threading").local()


def _use_zopfli() -> None:
    """Let zipfile use Zopfli for Deflate where this thread asks for it (``_zopfli_here.on``)."""
    if getattr(zipfile, "_wick_zopfli", False):
        return
    original = zipfile._get_compressor            # type: ignore[attr-defined]

    def get(compress_type, compresslevel=None):
        if compress_type == zipfile.ZIP_DEFLATED and getattr(_zopfli_here, "on", False):
            return _Zopfli()
        return original(compress_type, compresslevel)

    zipfile._get_compressor = get                 # type: ignore[attr-defined]
    zipfile._wick_zopfli = True                   # type: ignore[attr-defined]


def _picture_part(name: str, any_folder: bool) -> bool:
    return _is_picture(name) or (any_folder and name.lower().endswith((".png", ".jpg", ".jpeg")))


def optimize_office(src: Path, reduce: bool = False, long_edge: int = REDUCE_LONG_EDGE, fonts: bool = True,
                    crop: bool = False, pivots: bool = False, zopfli: bool = False) -> Path:
    """A smaller copy of ``src`` next to it (verified); the caller decides whether to keep it. ``fonts``: cut the
    embedded fonts; ``crop``: cut off what crops hide; ``pivots``: leave Excel's pivot caches to be rebuilt;
    ``zopfli``: denser (slower) compression for the text parts. E-books (.epub) and comic books (.cbz) too."""
    from . import officeslim

    office_pictures(src)        # raises Unsupported for signed / encrypted files
    ext = src.suffix.lower()
    book = ext in (".epub", ".cbz", ".zip")
    out = temp_name(src.parent, src.suffix)
    try:
        with zipfile.ZipFile(src) as zin:
            infos = zin.infolist()
            if any(i.flag_bits & 0x1 for i in infos):
                raise Unsupported("Password-protected")
            pkg = officeslim.Package([i.filename for i in infos], {i.filename: zin.read(i) for i in infos})
        if fonts:
            if ext == ".epub":
                try:
                    officeslim.epub_fonts(pkg)
                except officeslim.Protected as e:
                    raise Unsupported(str(e)) from e
            elif not book:
                _office_fonts(pkg)
        if not book:
            officeslim.remove_orphans(pkg)
            officeslim.bitmaps_to_png(pkg)
            if crop:
                officeslim.crop_pictures(pkg, lossy_ok=reduce)
            if pivots:
                officeslim.drop_pivot_caches(pkg)
        for name in pkg.names:
            if name in pkg.removed or name in pkg.renamed or not _picture_part(name, book):
                continue
            data = pkg.data[name]
            new = data
            pext = "." + name.lower().rsplit(".", 1)[-1]
            if reduce:
                new = reduce_picture(new, pext, long_edge)
            new = shrink_png(new) if pext == ".png" else shrink_jpeg(new)
            if new is not data and new != data:
                pkg.data[name] = new
                pkg.changed.add(name)
        if zopfli:
            _use_zopfli()
        with zipfile.ZipFile(out, "w") as zout:
            for info in infos:
                if info.filename in pkg.removed:
                    continue
                name = pkg.renamed.get(info.filename, info.filename)
                zi = zipfile.ZipInfo(name, info.date_time)
                zi.external_attr, zi.create_system, zi.comment = info.external_attr, info.create_system, info.comment
                zi.compress_type = info.compress_type     # the ODF and EPUB "mimetype" entry must stay stored
                text = zopfli and not _picture_part(name, True) and zi.compress_type == zipfile.ZIP_DEFLATED
                _zopfli_here.on = text
                try:
                    if zi.compress_type == zipfile.ZIP_DEFLATED:
                        zout.writestr(zi, pkg.data[name], compresslevel=9)
                    else:
                        zout.writestr(zi, pkg.data[name])
                finally:
                    _zopfli_here.on = False
        _verify_office(src, out, pkg, lossless=not reduce)
        return out
    except BaseException:
        remove_quietly(out)
        raise


def _verify_office(src: Path, out: Path, pkg, lossless: bool) -> None:
    from PIL import Image

    with zipfile.ZipFile(src) as a, zipfile.ZipFile(out) as b:
        if b.testzip() is not None:
            raise VerificationFailed("The rebuilt document doesn't read back correctly")
        expected = [pkg.renamed.get(i.filename, i.filename) for i in a.infolist() if i.filename not in pkg.removed]
        if expected != [i.filename for i in b.infolist()]:
            raise VerificationFailed("Parts of the document went missing")
        kept = [i for i in a.infolist() if i.filename not in pkg.removed]
        for ia, ib in zip(kept, b.infolist(), strict=True):
            if ib.filename not in pkg.changed and ia.filename not in pkg.changed:
                if ia.CRC != ib.CRC or ia.file_size != ib.file_size:
                    raise VerificationFailed(f"{ia.filename} changed")
                continue
            if not ib.filename.lower().endswith((".png", ".jpg", ".jpeg")):
                continue            # (a font cut and read back when it was made, or XML pointing at changes)
            old, new = a.read(ia), b.read(ib)
            try:
                with Image.open(io.BytesIO(new)) as im:
                    im.load()
            except Exception as e:  # noqa: BLE001
                raise VerificationFailed(f"Picture {ia.filename} doesn't open") from e
            if lossless and ib.filename not in pkg.cropped and not same_pixels(old, new):
                raise VerificationFailed(f"Picture {ia.filename} looks different")


# ---------------------------------------------------------------- PDFs

def pdf_problem(path: Path) -> str:
    """Why a PDF is left alone, or ""."""
    import pikepdf

    try:
        with pikepdf.open(path) as pdf:
            acro = pdf.Root.get("/AcroForm")
            if acro is not None and int(acro.get("/SigFlags", 0)) & 1:
                return "Digitally signed: any change would break the signature"
            for field in (acro.get("/Fields", []) if acro is not None else []):
                if field.get("/FT") == "/Sig":
                    return "Digitally signed: any change would break the signature"
    except pikepdf.PasswordError:
        return "Password-protected"
    except Exception as e:  # noqa: BLE001
        return f"Can't be read as a PDF ({type(e).__name__})"
    return ""


def _pdfa_part(pdf) -> int:
    try:
        with pdf.open_metadata() as meta:
            return int(meta.get("pdfaid:part", 0) or 0)
    except Exception:  # noqa: BLE001
        from wick.log import ignored
        ignored("documents._pdfa_part")
        return 0


def optimize_pdf(src: Path, reduce: bool = False, long_edge: int = REDUCE_LONG_EDGE, fonts: bool = True,
                 editing: bool = False) -> Path:
    """``fonts``: cut whole embedded fonts; ``editing``: leave out Illustrator's and Photoshop's editing data."""
    import pikepdf

    problem = pdf_problem(src)
    if problem:
        raise Unsupported(problem)
    out = temp_name(src.parent, ".pdf")
    try:
        with pikepdf.open(src) as pdf:
            pages = len(pdf.pages)
            boxes = [tuple(float(x) for x in p.mediabox) for p in pdf.pages]
            for obj in pdf.objects:
                if not isinstance(obj, pikepdf.Stream) or obj.get("/Subtype") != "/Image":
                    continue
                filt = obj.get("/Filter")
                if filt != "/DCTDecode" or "/DecodeParms" in obj or "/SMask" in obj and reduce:
                    continue
                raw = obj.read_raw_bytes()
                new = raw
                if reduce and "/SMask" not in obj and "/Mask" not in obj:
                    new = reduce_picture(new, ".jpg", long_edge)
                new = shrink_jpeg(new)
                if new == raw:
                    continue
                if new is not raw and reduce:
                    from PIL import Image

                    with Image.open(io.BytesIO(new)) as im:
                        w, h = im.size
                    obj.Width, obj.Height = w, h
                obj.write(new, filter=pikepdf.Name.DCTDecode)
            from . import pdfslim

            pdfslim.drop_thumbnails(pdf)                 # (page previews from early PDF versions: never shown)
            if editing:
                pdfslim.drop_editing_data(pdf)
            pikepdf.settings.set_flate_compression_level(9)
            mode = pikepdf.ObjectStreamMode.preserve if _pdfa_part(pdf) == 1 else pikepdf.ObjectStreamMode.generate
            pdf.save(out, object_stream_mode=mode, compress_streams=True, recompress_flate=True)
        with pikepdf.open(out) as check:
            if len(check.pages) != pages:
                raise VerificationFailed("Pages went missing")
            if [tuple(float(x) for x in p.mediabox) for p in check.pages] != boxes:
                raise VerificationFailed("Page sizes changed")
            # (renamed in pikepdf 9: older versions call it check)
            checker = getattr(check, "check_pdf_syntax", None) or getattr(check, "check", None)
            problems = [p for p in checker() if "error" in str(p).lower()] if checker is not None else []
            if problems:
                raise VerificationFailed(f"The rebuilt PDF has problems: {problems[0]}")
        return _pdf_structure(out, pages, fonts)
    except BaseException:
        remove_quietly(out)
        raise


def _pdf_structure(path: Path, pages: int, fonts: bool) -> Path:
    """``path`` with identical streams stored once, black-and-white scans as Group 4 and (``fonts``) whole embedded
    fonts cut to the glyphs drawn, when every page still draws exactly the same (else ``path`` as it was). Not
    for PDF/A files or long documents (each page is drawn twice to check)."""
    import pikepdf

    from . import docfonts, pdfslim

    if pages > docfonts.MAX_PAGES_TO_CHECK:
        return path
    smaller = temp_name(path.parent, ".pdf")
    try:
        with pikepdf.open(path) as pdf:
            if _pdfa_part(pdf):
                return path
            changed = pdfslim.dedupe_streams(pdf)
            changed += pdfslim.scans_to_group4(pdf)
            if fonts:
                changed += docfonts.cut_pdf_fonts(pdf)
            if not changed:
                return path
            pdf.save(smaller, object_stream_mode=pikepdf.ObjectStreamMode.generate, compress_streams=True)
        if smaller.stat().st_size < path.stat().st_size and docfonts.same_pages(path, smaller):
            remove_quietly(path)
            return smaller
    except Exception:  # noqa: BLE001 - the fonts stay whole
        from .log import log

        log.warning("Couldn't restructure %s; it stays as it was", path.name, exc_info=True)
    remove_quietly(smaller)
    return path


def optimize(src: Path, reduce: bool = False, fonts: bool = True, editing: bool = False, crop: bool = False,
             pivots: bool = False, zopfli: bool = False) -> Path:
    if src.suffix.lower() in PDF_EXTS:
        return optimize_pdf(src, reduce, fonts=fonts, editing=editing)
    return optimize_office(src, reduce, fonts=fonts, crop=crop, pivots=pivots, zopfli=zopfli)


__all__ = ["optimize", "optimize_office", "optimize_pdf", "office_pictures", "pdf_problem", "pdf_available",
           "Unsupported", "OFFICE_EXTS", "PDF_EXTS"]
