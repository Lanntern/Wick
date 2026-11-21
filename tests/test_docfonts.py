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
