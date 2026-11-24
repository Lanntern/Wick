"""The Convert page: per-file formats, documents, icons, PDFs, trimming, presets."""


import pytest
from PIL import Image

from .conftest import _wait


def test_convert_files_can_have_their_own_format(win, tmp_path, monkeypatch):
    from PySide6.QtCore import Qt

    from wick.gui import convert_settings

    for name in ("a.png", "b.png"):
        Image.new("RGB", (64, 48), "teal").save(tmp_path / name)
    conv = win.converter
    conv.clear()
    conv.image_target.setCurrentIndex(conv.image_target.findData("jpeg"))
    conv.add_paths([str(tmp_path)])
    assert _wait(lambda: not conv.busy() and conv.tree.topLevelItemCount() == 2, 30)
    entries = {e.path.name: e for e in conv.entries}
    menu = dict((x[0], x[1]) for x in conv.menu_entries([entries["b.png"]]) if x)
    webp = dict(menu["Convert To"])["WebP"]
    conv.tree.topLevelItem(0).setCheckState(0, Qt.Unchecked)       # (a left-out row stays left out)
    webp()
    rows = {conv.tree.topLevelItem(i).text(0): conv.tree.topLevelItem(i) for i in range(2)}
    assert rows["b.png"].text(4).startswith("WebP") and "own settings" in rows["b.png"].text(4)
    assert rows["a.png"].text(4).startswith("JPEG")
    assert sum(conv.tree.topLevelItem(i).checkState(0) == Qt.Checked for i in range(2)) == 1

    # The settings dialog changes only what was chosen for these files.
    def fake_exec(dlg):
        dlg.limit.setChecked(True)
        dlg.side.setValue(32)
        return dlg.DialogCode.Accepted
    monkeypatch.setattr(convert_settings.ConvertSettingsDialog, "exec", fake_exec)
    menu = dict((x[0], x[1]) for x in conv.menu_entries([entries["b.png"]]) if x)
    menu["Format and Settings…"]()
    assert conv.opts_for(entries["b.png"]).max_side == 32 and conv.opts_for(entries["b.png"]).image_target == "webp"
    assert conv.opts_for(entries["a.png"]).max_side == 0
    menu = dict((x[0], x[1]) for x in conv.menu_entries([entries["b.png"]]) if x)
    menu["Use the Page's Settings"]()
    assert conv.opts_for(entries["b.png"]).image_target == "jpeg"
    conv.clear()


def test_convert_lists_documents_and_offers_pandoc(win, tmp_path, monkeypatch):
    from wick import tools

    (tmp_path / "notes.md").write_text("# Notes\n")
    (tmp_path / "sheet.xlsx").write_bytes(b"PK")                      # (only an office app reads it: to PDF)
    real = tools.find_tool
    monkeypatch.setattr(tools, "find_tool", lambda n: None if n == "pandoc" else real(n))
    conv = win.converter
    conv.clear()
    conv.add_paths([str(tmp_path)])
    assert _wait(lambda: not conv.busy() and conv.tree.topLevelItemCount() == 2, 30)
    rows = {conv.tree.topLevelItem(i).text(0): conv.tree.topLevelItem(i) for i in range(2)}
    row = rows["notes.md"]
    assert row.text(conv.STATUS_COL) == "Needs Pandoc, see above"
    assert rows["sheet.xlsx"].text(conv.STATUS_COL) == "Only Office or LibreOffice can read this. Choose PDF instead."
    assert not conv.pandoc_row.isHidden() and "Pandoc" in conv.pandoc_note.text()
    monkeypatch.setattr(tools, "find_tool", real)
    conv.document_target.setCurrentIndex(conv.document_target.findData("none"))
    assert conv.pandoc_row.isHidden()                                  # (documents aren't being converted)
    conv.document_target.setCurrentIndex(conv.document_target.findData("docx"))
    conv.clear()


def test_icon_sizes_and_website_icons_from_convert(win, tmp_path, monkeypatch):
    from wick import convert, icons
    from wick.gui.convert_settings import IMAGE, ConvertSettingsDialog

    dlg = ConvertSettingsDialog(convert.ConvertOptions(image_target="ico"), {IMAGE}, "Settings", parent=win)
    assert dlg.ico_preset.currentData() == "web" and dlg.ico_preset.isVisibleTo(dlg)
    dlg.ico_preset.setCurrentIndex(dlg.ico_preset.findData("mediawiki"))
    assert dlg._ico_sizes() == (16, 32, 48, 64)
    dlg.ico_boxes[256].setChecked(True)                                # a size of your own: "Choose sizes"
    assert dlg.ico_preset.currentData() == "custom"
    assert dlg.result_options().ico_sizes == (16, 32, 48, 64, 256)
    dlg.image_target.setCurrentIndex(dlg.image_target.findData("png"))
    assert not dlg.ico_preset.isVisibleTo(dlg)                         # (only for icons)
    dlg.deleteLater()

    Image.new("RGB", (64, 64), "teal").save(tmp_path / "logo.png")
    conv = win.converter
    conv.clear()
    conv.add_paths([str(tmp_path)])
    assert _wait(lambda: not conv.busy() and conv.tree.topLevelItemCount() == 1, 30)
    shown = []
    from wick.gui import dialogs
    monkeypatch.setattr(dialogs, "choose", lambda _p, text, info, *a: shown.append((text, info)) or False)
    menu = dict((x[0], x[1]) for x in conv.menu_entries(conv.entries) if x)
    menu["Make Website Icons…"]()
    out = tmp_path / "logo website icons"
    assert sorted(p.name for p in out.iterdir()) == sorted(n for n in icons.WEB_FILES if n != "icon.svg")
    assert shown and "<link rel=\"icon\"" in shown[0][1]
    from PySide6.QtWidgets import QApplication

    assert 'rel="manifest"' in QApplication.clipboard().text()
    conv.clear()


def test_convert_from_the_context_menu(win, tmp_path):
    """Explorer's Convert with Wick ▸ JPEG: one launch per selected file, collected, converted next to
    the originals and started without asking; files already listed are left as they were."""
    from wick.gui import integration

    for name in ("a.png", "b.png", "keep.png"):
        Image.new("RGB", (64, 48), "teal").save(tmp_path / name)
    conv = win.converter
    conv.clear()
    smaller = conv.smaller.isChecked()
    conv.smaller.setChecked(False)         # this test deliberately exports tiny PNGs as larger JPEGs
    conv.output.setCurrentIndex(conv.output.findData("suffix"))       # (the page's choice doesn't apply here)
    conv.add_paths([str(tmp_path / "keep.png")])
    assert _wait(lambda: not conv.busy() and conv.tree.topLevelItemCount() == 1, 30)
    win.receive_paths([str(tmp_path / "a.png")], show=False, convert="jpeg")
    win.receive_paths([str(tmp_path / "b.png")], show=False, convert="jpeg")
    assert win.pages.currentWidget() is conv
    assert _wait(lambda: (tmp_path / "a.jpg").exists() and (tmp_path / "b.jpg").exists() and not conv.busy(), 60)
    assert not (tmp_path / "keep.jpg").exists() and (tmp_path / "a.png").exists()   # originals kept
    assert integration.CONVERT_MENUS["image"][0] == ("JPEG", "jpeg")
    labels = [label for label, _ in integration._convert_items("video")]
    assert labels[-1] == "Other Format…" and "GIF" in labels
    conv.output.setCurrentIndex(conv.output.findData("beside"))
    conv.smaller.setChecked(smaller)
    conv.clear()


def test_rows_that_become_convertible_are_ticked(win, tmp_path):
    from PySide6.QtCore import Qt

    Image.new("RGB", (64, 48), "teal").save(tmp_path / "a.png")
    conv = win.converter
    conv.clear()
    conv.image_target.setCurrentIndex(conv.image_target.findData("png"))
    conv.add_paths([str(tmp_path)])
    assert _wait(lambda: not conv.busy() and conv.tree.topLevelItemCount() == 1, 30)
    assert conv.tree.topLevelItem(0).text(conv.STATUS_COL).startswith("Already")
    conv.image_target.setCurrentIndex(conv.image_target.findData("webp"))
    assert conv.tree.topLevelItem(0).checkState(0) == Qt.Checked        # (it wasn't left out by you)
    conv.clear()


def test_convert_pdf_actions_and_combining(win, tmp_path, monkeypatch):
    from PySide6.QtWidgets import QFileDialog

    from wick import pdftools
    from wick.gui import convert_tab, dialogs

    Image.new("RGB", (64, 48), "teal").save(tmp_path / "a.png")
    Image.new("RGB", (48, 64), "red").save(tmp_path / "b.jpg", quality=90)
    pdftools.combine([tmp_path / "a.png"], tmp_path / "doc.pdf")
    conv = win.converter
    conv.clear()
    conv.add_paths([str(tmp_path)])
    assert _wait(lambda: not conv.busy() and conv.tree.topLevelItemCount() == 3, 30)
    entries = {e.path.name: e for e in conv.entries}
    assert entries["doc.pdf"].kind == "pdf"
    menu = dict(x for x in conv.menu_entries([entries["doc.pdf"]]) if x)
    pdf_menu = dict(x for x in menu["PDF"] if x)
    monkeypatch.setattr(convert_tab.ConvertTab, "_ask_password", lambda self, *a, **k: "s3cret")
    pdf_menu["Add a Password…"]()
    o = conv.opts_for(entries["doc.pdf"])
    assert o.pdf_target == "protect" and o.pdf_password == "s3cret"
    row = next(conv.tree.topLevelItem(i) for i in range(3) if conv.tree.topLevelItem(i).text(0) == "doc.pdf")
    assert row.text(4).startswith("With a password")

    dest = tmp_path / "both.pdf"
    monkeypatch.setattr(QFileDialog, "getSaveFileName", lambda *a, **k: (str(dest), ""))
    monkeypatch.setattr(dialogs, "choose", lambda *a, **k: False)
    menu = dict(x for x in conv.menu_entries([entries["b.jpg"], entries["a.png"]]) if x)
    menu["Combine Into One PDF…"]()
    assert _wait(lambda: dest.exists() and not conv.busy(), 30)
    assert pdftools.page_count(dest) == 2
    conv.clear()


def test_turning_from_the_convert_menu_adds_up(win, tmp_path):
    Image.new("RGB", (64, 48), "teal").save(tmp_path / "a.jpg")
    conv = win.converter
    conv.clear()
    conv.add_paths([str(tmp_path)])
    assert _wait(lambda: not conv.busy() and conv.tree.topLevelItemCount() == 1, 30)
    e = conv.entries[0]

    def do(label):
        menu = dict(x for x in conv.menu_entries([e]) if x)
        dict(x for x in menu["Rotate"] if x)[label]()

    do("Right")
    assert (conv.opts_for(e).turn, conv.opts_for(e).image_target) == (90, "keep")
    do("Right")
    assert conv.opts_for(e).turn == 180
    do("Mirror")                                   # after an upside-down turn, a mirror is still left to right
    assert conv.opts_for(e).flip == "h"
    do("Flip Upside Down")                         # both mirrors: turned upside down again, no mirror
    assert (conv.opts_for(e).turn, conv.opts_for(e).flip) == (0, "")
    assert "Nothing to change" in conv.tree.topLevelItem(0).text(conv.STATUS_COL)
    conv.clear()


def test_trim_from_convert(win, tmp_path, monkeypatch):
    from wick.gui import dialogs
    from wick.gui.trim_dialog import TrimDialog, clock, parse_clock

    from ..conftest import ffmpeg, have

    if not have("ffmpeg", "ffprobe"):
        pytest.skip("ffmpeg not installed")
    assert parse_clock("1:23.4") == 83.4 and parse_clock("0:01:00") == 60 and parse_clock("x") is None
    assert clock(83.44) == "1:23.4"
    ffmpeg("-f", "lavfi", "-i", "sine=d=4", str(tmp_path / "song.mp3"))
    conv = win.converter
    conv.clear()
    conv.add_paths([str(tmp_path)])
    assert _wait(lambda: not conv.busy() and conv.tree.topLevelItemCount() == 1, 30)

    def fake_exec(dlg):
        assert not dlg.save.isEnabled()                       # (nothing cut off yet)
        dlg.fields[0].setText("1")
        dlg._typed(0)
        dlg.sliders[1].setValue(30)
        assert dlg.times() == (1.0, 3.0) and dlg.save.isEnabled()
        return dlg.DialogCode.Accepted
    monkeypatch.setattr(TrimDialog, "exec", fake_exec)
    monkeypatch.setattr(dialogs, "choose", lambda *a, **k: False)
    menu = dict(x for x in conv.menu_entries(conv.entries) if x)
    menu["Trim…"]()
    out = tmp_path / "song (trimmed).mp3"
    assert _wait(lambda: out.exists() and not conv.busy(), 30)
    from wick.cutjoin import duration_of

    assert abs(duration_of(out) - 2.0) < 0.1
    conv.clear()


def test_convert_presets_from_the_page_and_the_context_menu(win, tmp_path, monkeypatch):
    from PySide6.QtWidgets import QInputDialog

    from wick import engine, journal
    from wick.gui import convert_presets, integration

    conv = win.converter
    conv.clear()
    conv.image_target.setCurrentIndex(conv.image_target.findData("webp"))
    conv.output.setCurrentIndex(conv.output.findData("beside"))
    conv.originals.setCurrentIndex(conv.originals.findData("move"))
    conv.originals_dir.setText(str(tmp_path / "done"))
    monkeypatch.setattr(QInputDialog, "getText", lambda *a, **k: ("Web pictures", True))
    conv._save_preset()
    presets = convert_presets.load(conv.qs)
    assert "image" in convert_presets.kinds(presets["Web pictures"])     # (and whatever else the page converts)
    assert "originals moved to a folder" in convert_presets.describe(presets["Web pictures"])
    labels = [label for label, _ in integration._convert_items("image", presets)]
    assert "Web pictures" in labels and labels[-1] == "Other Format…"

    # Change the page, then run the preset from Explorer: its own choices apply, not the page's.
    conv.image_target.setCurrentIndex(conv.image_target.findData("png"))
    conv.originals.setCurrentIndex(conv.originals.findData("keep"))
    Image.new("RGB", (64, 48), "teal").save(tmp_path / "a.jpg")
    win.receive_paths([str(tmp_path / "a.jpg")], show=False, convert="preset:Web pictures")
    assert _wait(lambda: (tmp_path / "a.webp").exists() and not conv.busy(), 60)
    assert not (tmp_path / "a.jpg").exists() and (tmp_path / "done" / "a.jpg").exists()   # the original moved
    run = journal.list_runs()[0]
    engine.undo_run(run.run_id)
    assert (tmp_path / "a.jpg").exists() and not (tmp_path / "a.webp").exists()          # and came back

    assert conv.apply_preset("Web pictures")
    assert conv.image_target.currentData() == "webp" and conv.originals.currentData() == "move"
    conv._delete_preset("Web pictures")
    assert "Web pictures" not in convert_presets.load(conv.qs)
    conv.originals.setCurrentIndex(conv.originals.findData("keep"))
    conv.clear()
