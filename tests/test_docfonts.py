"""Fonts embedded in Word files and PDFs, cut to what the document uses."""

import zipfile

import pytest

from wick import docfonts, documents

from .test_subfonts import CJK, make_font

KEY = "6C3D2B1A-9F8E-4D7C-B6A5-0123456789AB"


def _word_file(tmp_path, scrambled: bool = True):
    font = make_font(tmp_path / "f.ttf", "Test Sans", CJK).read_bytes()
    key = bytes.fromhex(KEY.replace("-", ""))[::-1]
    data = docfonts._xor(font, key) if scrambled else font
    doc = tmp_path / "letter.docx"
    with zipfile.ZipFile(doc, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("[Content_Types].xml", '<?xml version="1.0"?><Types/>')
        z.writestr("word/document.xml", '<w:document><w:body><w:p><w:r><w:rPr><w:rFonts w:ascii="Test Sans"/>'
                   '</w:rPr><w:t>Dear 世中 &amp; friends</w:t></w:r><w:r><w:sym w:font="Wingdings" w:char="F04A"/>'
                   '</w:r></w:p></w:body></w:document>')
        z.writestr("word/fontTable.xml", '<w:fonts><w:font w:name="Test Sans"><w:embedRegular r:id="rId1" '
                   f'w:fontKey="{{{KEY}}}" w:subsetted="0"/></w:font></w:fonts>')
        z.writestr("word/fonts/font1.odttf", data)
    return doc, font


def test_a_word_files_scrambled_font_is_cut_and_scrambled_again(tmp_path):
    from io import BytesIO

    from fontTools.ttLib import TTFont

    doc, font = _word_file(tmp_path)
    out = documents.optimize_office(doc)
    try:
        with zipfile.ZipFile(out) as z:
            stored = z.read("word/fonts/font1.odttf")
            table = z.read("word/fontTable.xml").decode()
            assert z.read("word/document.xml") == zipfile.ZipFile(doc).read("word/document.xml")
        assert len(stored) < len(font) / 5
        assert 'w:subsetted="1"' in table and 'w:subsetted="0"' not in table
        key = bytes.fromhex(KEY.replace("-", ""))[::-1]
        cmap = set(TTFont(BytesIO(docfonts._xor(stored, key))).getBestCmap())
        assert {ord(c) for c in "Dear世中&friends"} <= cmap and 0x4E00 not in cmap and ord("J") in cmap
    finally:
        out.unlink(missing_ok=True)
    kept = documents.optimize_office(doc, fonts=False)
    try:
        with zipfile.ZipFile(kept) as z:
            assert z.read("word/fonts/font1.odttf") == zipfile.ZipFile(doc).read("word/fonts/font1.odttf")
    finally:
        kept.unlink(missing_ok=True)


def test_an_unreadable_font_part_stays_as_it_is(tmp_path):
    doc = tmp_path / "odd.pptx"
    with zipfile.ZipFile(doc, "w") as z:
        z.writestr("ppt/slides/slide1.xml", "<p:sld><a:t>Hi</a:t></p:sld>")
        z.writestr("ppt/fonts/font1.fntdata", b"\x00not a font" * 500)
    assert documents._office_fonts(zipfile.ZipFile(doc)) == ({}, 0)


def _pdf_with_font(tmp_path, kind: str):
    """A one-page PDF showing text with a font embedded whole (simple TrueType, or Type0 with Identity-H)."""
    import pikepdf

    data = make_font(tmp_path / f"{kind}.ttf", "Test Sans", CJK).read_bytes()
    pdf = pikepdf.new()
    program = pikepdf.Stream(pdf, data)
    program.Length1 = len(data)
    desc = pikepdf.Dictionary(Type=pikepdf.Name.FontDescriptor, FontName=pikepdf.Name("/TestSans"), Flags=32,
                              FontBBox=[0, -200, 1000, 800], ItalicAngle=0, Ascent=800, Descent=-200, CapHeight=700,
                              StemV=80, FontFile2=pdf.make_indirect(program))
    if kind == "simple":
        font = pikepdf.Dictionary(Type=pikepdf.Name.Font, Subtype=pikepdf.Name.TrueType,
                                  BaseFont=pikepdf.Name("/TestSans"), FirstChar=32, LastChar=126,
                                  Widths=[500] * 95, Encoding=pikepdf.Name.WinAnsiEncoding,
                                  FontDescriptor=pdf.make_indirect(desc))
        text = b"BT /F1 36 Tf 40 700 Td (Hello PDF) Tj ET"
    else:
        cid = pikepdf.Dictionary(Type=pikepdf.Name.Font, Subtype=pikepdf.Name.CIDFontType2,
                                 BaseFont=pikepdf.Name("/TestSans"), CIDToGIDMap=pikepdf.Name.Identity,
                                 CIDSystemInfo=pikepdf.Dictionary(Registry="Adobe", Ordering="Identity", Supplement=0),
                                 DW=500, FontDescriptor=pdf.make_indirect(desc))
        font = pikepdf.Dictionary(Type=pikepdf.Name.Font, Subtype=pikepdf.Name.Type0,
                                  BaseFont=pikepdf.Name("/TestSans"), Encoding=pikepdf.Name("/Identity-H"),
                                  DescendantFonts=[pdf.make_indirect(cid)])
        text = b"BT /F1 36 Tf 40 700 Td <0029004600550055> Tj ET"     # four glyphs by number
    page = pdf.add_blank_page(page_size=(612, 792))
    page.obj.Resources = pikepdf.Dictionary(Font=pikepdf.Dictionary(F1=pdf.make_indirect(font)))
    page.obj.Contents = pdf.make_stream(text)
    path = tmp_path / f"{kind}.pdf"
    pdf.save(path)
    return path, len(data)


@pytest.mark.parametrize("kind", ["simple", "cid"])
def test_a_pdfs_whole_font_is_cut_and_the_page_looks_the_same(tmp_path, kind):
    import pikepdf

    src, font_size = _pdf_with_font(tmp_path, kind)
    out = documents.optimize_pdf(src)
    try:
        assert docfonts.same_pages(src, out)
        with pikepdf.open(out) as pdf:
            fonts = [o for o in pdf.objects if isinstance(o, pikepdf.Dictionary) and "/FontFile2" in o]
            assert len(fonts) == 1
            assert len(fonts[0].FontFile2.read_bytes()) < font_size / 5
            assert str(fonts[0].FontName)[7] == "+"                   # tagged as a subset, as the standard asks
        assert out.stat().st_size < src.stat().st_size / 3
    finally:
        out.unlink(missing_ok=True)


def test_different_pages_are_noticed(tmp_path):
    import pikepdf

    a, _ = _pdf_with_font(tmp_path, "simple")           # (every glyph is the same box: change the length)
    with pikepdf.open(a) as pdf:
        pdf.pages[0].obj.Contents = pdf.make_stream(b"BT /F1 36 Tf 40 700 Td (Hello PD) Tj ET")
        pdf.save(tmp_path / "b.pdf")
    assert docfonts.same_pages(a, a) and not docfonts.same_pages(a, tmp_path / "b.pdf")


def _blank_pdf(tmp_path, name="a.pdf", pages=2):
    import pikepdf

    pdf = pikepdf.new()
    for _ in range(pages):
        pdf.add_blank_page(page_size=(300, 300))
    return pdf


def _scan(w=2480, h=3508):
    """An A4 page of text scanned at 300 dpi: black on white, one bit per pixel."""
    import random

    from PIL import Image, ImageDraw, ImageFont

    rnd = random.Random(5)
    img = Image.new("L", (w, h), 255)
    draw = ImageDraw.Draw(img)
    font = ImageFont.load_default(size=40)
    for y in range(150, h - 150, 60):
        line = "".join(rnd.choice("abcdefghijklmnopqrstuvwxyz     ") for _ in range(100))
        draw.text((150, y), line, fill=0, font=font)
    return img.point(lambda v: 255 if v > 128 else 0).convert("1")


def _image_page(pdf, page, img):
    import zlib

    import pikepdf

    raw = img.tobytes()                     # (1 bit per pixel, rows padded to bytes: as PDF expects)
    stream = pikepdf.Stream(pdf, zlib.compress(raw, 1))
    stream.Type, stream.Subtype = pikepdf.Name.XObject, pikepdf.Name.Image
    stream.Width, stream.Height, stream.BitsPerComponent = img.width, img.height, 1
    stream.ColorSpace, stream.Filter = pikepdf.Name.DeviceGray, pikepdf.Name.FlateDecode
    page.obj.Resources = pikepdf.Dictionary(XObject=pikepdf.Dictionary(Im0=pdf.make_indirect(stream)))
    page.obj.Contents = pdf.make_stream(b"q 200 0 0 150 50 50 cm /Im0 Do Q")


def test_scans_duplicates_thumbnails_and_editing_data(tmp_path):
    import pikepdf

    pdf = _blank_pdf(tmp_path)
    for page in pdf.pages:                  # (the same scan stored twice, as merged PDFs do)
        _image_page(pdf, page, _scan())
        page.obj.Thumb = pdf.make_stream(b"\0" * 20000)
    private = pdf.make_stream(b"Illustrator private data " * 20000)
    pdf.Root.PieceInfo = pikepdf.Dictionary(Illustrator=pikepdf.Dictionary(Private=pikepdf.Dictionary(
        AIPrivateData1=private)))
    src = tmp_path / "scan.pdf"
    pdf.save(src, compress_streams=False)
    out = documents.optimize_pdf(src, editing=True)
    try:
        assert docfonts.same_pages(src, out)
        with pikepdf.open(out) as o:
            images = [x for x in o.objects if isinstance(x, pikepdf.Stream) and x.get("/Subtype") == "/Image"]
            assert len(images) == 1 and images[0].Filter == "/CCITTFaxDecode"
            assert "/PieceInfo" not in o.Root and all("/Thumb" not in p.obj for p in o.pages)
        assert out.stat().st_size < src.stat().st_size / 5
    finally:
        out.unlink(missing_ok=True)
    kept = documents.optimize_pdf(src)                                  # (editing data is kept by default)
    try:
        with pikepdf.open(kept) as o:
            assert "/PieceInfo" in o.Root
    finally:
        kept.unlink(missing_ok=True)


def _cff_font(path):
    from fontTools.fontBuilder import FontBuilder
    from fontTools.pens.t2CharStringPen import T2CharStringPen

    codes = [*range(0x20, 0x7F), *range(0x4E00, 0x5200)]
    names = [".notdef"] + [f"u{c:04X}" for c in codes]

    def box():
        pen = T2CharStringPen(500, None)
        pen.moveTo((50, 0)), pen.lineTo((50, 700)), pen.lineTo((450, 700)), pen.lineTo((450, 0)), pen.closePath()
        return pen.getCharString()

    fb = FontBuilder(1000, isTTF=False)
    fb.setupGlyphOrder(names)
    fb.setupCharacterMap({c: f"u{c:04X}" for c in codes})
    fb.setupCFF("TestCFF", {"FullName": "Test CFF"}, {n: box() for n in names}, {})
    fb.setupHorizontalMetrics({n: (500, 50) for n in names})
    fb.setupHorizontalHeader(ascent=800, descent=-200)
    fb.setupNameTable({"familyName": "Test CFF", "styleName": "Regular"})
    fb.setupOS2()
    fb.setupPost()
    fb.save(path)
    return path


def test_a_whole_opentype_cff_font_is_cut_too(tmp_path):
    import pikepdf

    data = _cff_font(tmp_path / "cff.otf").read_bytes()
    pdf = pikepdf.new()
    program = pikepdf.Stream(pdf, data)
    program.Subtype = pikepdf.Name.OpenType
    desc = pikepdf.Dictionary(Type=pikepdf.Name.FontDescriptor, FontName=pikepdf.Name("/TestCFF"), Flags=32,
                              FontBBox=[0, -200, 1000, 800], ItalicAngle=0, Ascent=800, Descent=-200, CapHeight=700,
                              StemV=80, FontFile3=pdf.make_indirect(program))
    font = pikepdf.Dictionary(Type=pikepdf.Name.Font, Subtype=pikepdf.Name.Type1, BaseFont=pikepdf.Name("/TestCFF"),
                              FirstChar=32, LastChar=126, Widths=[500] * 95, Encoding=pikepdf.Name.WinAnsiEncoding,
                              FontDescriptor=pdf.make_indirect(desc))
    page = pdf.add_blank_page(page_size=(612, 792))
    page.obj.Resources = pikepdf.Dictionary(Font=pikepdf.Dictionary(F1=pdf.make_indirect(font)))
    page.obj.Contents = pdf.make_stream(b"BT /F1 36 Tf 40 700 Td (Hello CFF) Tj ET")
    src = tmp_path / "cff.pdf"
    pdf.save(src)
    out = documents.optimize_pdf(src)
    try:
        assert docfonts.same_pages(src, out)
        with pikepdf.open(out) as o:
            stored = [x for x in o.objects if isinstance(x, pikepdf.Dictionary) and "/FontFile3" in x][0]
            assert len(stored.FontFile3.read_bytes()) < len(data) / 3 and str(stored.FontName)[7] == "+"
    finally:
        out.unlink(missing_ok=True)
