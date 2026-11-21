"""Office files, e-books and comic books: unused parts, BMP and TIFF pictures, crops, pivot caches, e-book fonts,
comic pages and denser compression."""

import hashlib
import io
import zipfile

from wick import documents, officeslim

CT = ('<?xml version="1.0"?><Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
      '<Default Extension="xml" ContentType="application/xml"/>{overrides}</Types>')


def _png(w=600, h=400, seed=0) -> bytes:
    import numpy as np
    from PIL import Image

    rng = np.random.default_rng(seed)
    img = np.repeat(np.repeat(rng.integers(0, 255, (h // 20, w // 20, 3), dtype=np.uint8), 20, 0), 20, 1)
    buf = io.BytesIO()
    Image.fromarray(img).save(buf, "PNG", compress_level=0)
    return buf.getvalue()


def _bmp() -> bytes:
    from PIL import Image

    buf = io.BytesIO()
    Image.open(io.BytesIO(_png(300, 200, 2))).save(buf, "BMP")
    return buf.getvalue()


def _docx(path, parts: dict[str, bytes | str]):
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as z:
        for name, data in parts.items():          # (an e-book's "mimetype" comes first, uncompressed)
            z.writestr(name, data, compress_type=zipfile.ZIP_STORED if name == "mimetype" else None)
    return path


def test_unused_pictures_go_and_bitmaps_become_png(tmp_path):
    doc = _docx(tmp_path / "report.docx", {
        "[Content_Types].xml": CT.format(overrides='<Override PartName="/word/media/old.png" '
                                                   'ContentType="image/png"/>'),
        "word/document.xml": '<w:document><w:body><w:p><w:r><w:t>Hello</w:t></w:r></w:p></w:body></w:document>',
        "word/_rels/document.xml.rels": '<Relationships><Relationship Id="rId1" Type="x/image" '
                                        'Target="media/used.png"/><Relationship Id="rId2" Type="x/image" '
                                        'Target="media/scan.bmp"/></Relationships>',
        "word/media/used.png": _png(seed=1), "word/media/old.png": _png(seed=2), "word/media/scan.bmp": _bmp(),
    })
    out = documents.optimize_office(doc)
    try:
        with zipfile.ZipFile(out) as z:
            names = z.namelist()
            rels = z.read("word/_rels/document.xml.rels").decode()
            ct = z.read("[Content_Types].xml").decode()
            png = z.read("word/media/scan.png")
        assert "word/media/old.png" not in names and "word/media/used.png" in names
        assert "word/media/scan.bmp" not in names and 'Target="media/scan.png"' in rels
        assert "old.png" not in ct and 'Extension="png"' in ct
        from PIL import Image

        with Image.open(io.BytesIO(png)) as a, Image.open(io.BytesIO(_bmp())) as b:
            assert a.convert("RGB").tobytes() == b.convert("RGB").tobytes()
    finally:
        out.unlink(missing_ok=True)


def test_a_cropped_picture_loses_its_hidden_part(tmp_path):
    slide = ('<p:sld><p:pic><p:blipFill><a:blip r:embed="rId2"/><a:srcRect l="25000" r="25000"/><a:stretch>'
             '<a:fillRect/></a:stretch></p:blipFill></p:pic></p:sld>')
    doc = _docx(tmp_path / "talk.pptx", {
        "[Content_Types].xml": CT.format(overrides=""),
        "ppt/slides/slide1.xml": slide,
        "ppt/slides/_rels/slide1.xml.rels": '<Relationships><Relationship Id="rId2" Type="x/image" '
                                            'Target="../media/image1.png"/></Relationships>',
        "ppt/media/image1.png": _png(),
    })
    assert zipfile.ZipFile(documents.optimize_office(doc)).read("ppt/media/image1.png")   # (off: kept whole)
    out = documents.optimize_office(doc, crop=True)
    try:
        from PIL import Image

        with zipfile.ZipFile(out) as z:
            with Image.open(io.BytesIO(z.read("ppt/media/image1.png"))) as im:
                assert im.size == (300, 400)
            assert '<a:srcRect/>' in z.read("ppt/slides/slide1.xml").decode()
    finally:
        out.unlink(missing_ok=True)


def test_pivot_caches_are_left_for_excel_to_rebuild(tmp_path):
    records = "<pivotCacheRecords>" + "<r><n v='1'/></r>" * 20000 + "</pivotCacheRecords>"
    book = _docx(tmp_path / "sales.xlsx", {
        "[Content_Types].xml": CT.format(overrides='<Override PartName="/xl/pivotCache/pivotCacheRecords1.xml" '
                                                   'ContentType="x"/>'),
        "xl/workbook.xml": "<workbook/>",
        "xl/pivotCache/pivotCacheDefinition1.xml": '<pivotCacheDefinition xmlns:r="r" r:id="rId1" '
                                                   'refreshOnLoad="0"><cacheSource type="worksheet">'
                                                   '<worksheetSource ref="A1:B9" sheet="Data"/></cacheSource>'
                                                   '</pivotCacheDefinition>',
        "xl/pivotCache/_rels/pivotCacheDefinition1.xml.rels": '<Relationships><Relationship Id="rId1" '
                                                              'Type="http://x/pivotCacheRecords" '
                                                              'Target="pivotCacheRecords1.xml"/></Relationships>',
        "xl/pivotCache/pivotCacheRecords1.xml": records,
    })
    out = documents.optimize_office(book, pivots=True)
    try:
        with zipfile.ZipFile(out) as z:
            assert "xl/pivotCache/pivotCacheRecords1.xml" not in z.namelist()
            definition = z.read("xl/pivotCache/pivotCacheDefinition1.xml").decode()
            assert 'refreshOnLoad="1"' in definition and "r:id" not in definition
            assert "pivotCacheRecords1" not in z.read("[Content_Types].xml").decode()
    finally:
        out.unlink(missing_ok=True)


def test_an_epubs_scrambled_font_is_cut(tmp_path):
    from fontTools.ttLib import TTFont

    from .test_subfonts import CJK, make_font

    font = make_font(tmp_path / "f.ttf", "Book Serif", CJK).read_bytes()
    ident = "urn:uuid:12345678-1234-1234-1234-123456789abc"
    key = hashlib.sha1(ident.encode()).digest()
    scrambled = officeslim._xor(font, key, 1040)
    book = _docx(tmp_path / "novel.epub", {
        "mimetype": "application/epub+zip",
        "META-INF/container.xml": '<container><rootfiles><rootfile full-path="OEBPS/content.opf"/></rootfiles>'
                                  '</container>',
        "META-INF/encryption.xml": '<encryption><EncryptedData><EncryptionMethod Algorithm="http://www.idpf.org/'
                                   '2008/embedding"/><CipherData><CipherReference URI="OEBPS/fonts/f.ttf"/>'
                                   '</CipherData></EncryptedData></encryption>',
        "OEBPS/content.opf": f'<package unique-identifier="uid"><metadata><dc:identifier id="uid">{ident}'
                             '</dc:identifier></metadata></package>',
        "OEBPS/ch1.xhtml": "<html><body><p>Once upon a 世 time.</p></body></html>",
        "OEBPS/fonts/f.ttf": scrambled,
        "OEBPS/images/cover.png": _png(seed=4),
    })
    out = documents.optimize_office(book)
    try:
        with zipfile.ZipFile(out) as z:
            first = z.infolist()[0]
            assert first.filename == "mimetype" and first.compress_type == zipfile.ZIP_STORED
            stored = z.read("OEBPS/fonts/f.ttf")
            cover = z.read("OEBPS/images/cover.png")
        assert len(stored) < len(font) / 5 and len(cover) < len(_png(seed=4))
        cmap = set(TTFont(io.BytesIO(officeslim._xor(stored, key, 1040))).getBestCmap())
        assert {ord(c) for c in "Onceuponatime世."} <= cmap and 0x4E00 not in cmap
    finally:
        out.unlink(missing_ok=True)


def test_a_drm_protected_epub_is_left_alone(tmp_path):
    import pytest

    book = _docx(tmp_path / "locked.epub", {
        "mimetype": "application/epub+zip",
        "META-INF/encryption.xml": '<encryption><EncryptedData><EncryptionMethod Algorithm="http://www.w3.org/2001/'
                                   '04/xmlenc#aes128-cbc"/><CipherData><CipherReference URI="OEBPS/ch1.xhtml"/>'
                                   '</CipherData></EncryptedData></encryption>',
        "OEBPS/ch1.xhtml": "x", "OEBPS/fonts/f.ttf": b"x" * 1000,
    })
    with pytest.raises(documents.Unsupported):
        documents.optimize_office(book)


def test_comic_pages_shrink_with_the_same_pixels(tmp_path):
    from wick.formats import COMIC, DOCUMENT, classify
    from wick.scanner import _kind
    from wick.settings import Settings

    comic = _docx(tmp_path / "issue1.cbz", {f"{i:03d}.png": _png(seed=i) for i in range(3)})
    assert classify(comic) == DOCUMENT and _kind(comic, Settings(convert_inputs=True)) == COMIC
    out = documents.optimize_office(comic)
    try:
        assert out.stat().st_size < comic.stat().st_size * 0.8
    finally:
        out.unlink(missing_ok=True)


def test_zopfli_packs_the_text_tighter(tmp_path):
    xml = "<w:document>" + "".join(f"<w:p><w:r><w:t>Line {i} of a long report.</w:t></w:r></w:p>"
                                   for i in range(4000)) + "</w:document>"
    doc = _docx(tmp_path / "long.docx", {"[Content_Types].xml": CT.format(overrides=""), "word/document.xml": xml,
                                         "word/media/a.png": _png(seed=9)})
    plain, dense = documents.optimize_office(doc), None
    try:
        dense = documents.optimize_office(doc, zopfli=True)
        with zipfile.ZipFile(dense) as z:
            assert z.read("word/document.xml").decode() == xml
            info = z.getinfo("word/document.xml")
        assert info.compress_size < zipfile.ZipFile(plain).getinfo("word/document.xml").compress_size
    finally:
        plain.unlink(missing_ok=True)
        if dense is not None:
            dense.unlink(missing_ok=True)
