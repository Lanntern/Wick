"""The compression settings panel (left side of the Compress tab)."""

from __future__ import annotations

import os

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QFileDialog, QFormLayout, QHBoxLayout, QLabel, QLineEdit, QMenu,
    QPushButton, QSpinBox, QStackedWidget, QVBoxLayout, QWidget,
)

from ..settings import Settings
from ..fileops import BIN_NAME  # noqa: F401  (others import it from here too)
from .style import Size, Space, Type, text_scale
from .widgets import Section, SizeInput, button, text

METADATA_CHOICES = [("Keep all", "keep"), ("Remove location", "strip_gps"),
                    ("Remove location & personal info", "strip_private"),
                    ("Remove all except date taken", "strip_all_keep_date"), ("Remove all", "strip_all")]
OUTPUT_CHOICES = [("Replace originals", "replace"), ("Next to originals", "beside"),
                  ("Save with a new name", "suffix"), ("Save to another folder", "folder")]
NAME_TIP = ("Text is added to the end of the name, like _compressed. You can also use a pattern such as %name% "
            "%date%. Available: %name%, %ext%, %date%, %time%, %datetime%, %unix% and %modified%, the file's "
            "modified date.")


def _combo(items: list[tuple[str, str]], tip: str = "") -> QComboBox:
    c = QComboBox()
    # Don't let long option texts widen the panel; the popup still shows them in full.
    c.setSizeAdjustPolicy(QComboBox.AdjustToMinimumContentsLengthWithIcon)
    c.setMinimumContentsLength(8)
    for label, value in items:
        c.addItem(label, value)
    if tip:
        c.setToolTip(tip)
    return c


def _described(combo: QComboBox, help_by_value: dict[str, str]) -> QLabel:
    """A line under ``combo`` saying what the chosen option means (and the same on each option in its list),
    for choices whose names mean little to most people."""
    for i in range(combo.count()):
        tip = help_by_value.get(combo.itemData(i), "")
        if tip:
            combo.setItemData(i, tip, Qt.ToolTipRole)
    label = text("", Type.FOOTNOTE, secondary=True, wrap=True)

    def show(_i: int = 0) -> None:
        label.setText(help_by_value.get(combo.currentData(), ""))
        combo.setAccessibleDescription(label.text())

    combo.currentIndexChanged.connect(show)
    show()
    return label


def _set_combo(c: QComboBox, value) -> None:
    i = c.findData(value)
    if i >= 0:
        c.setCurrentIndex(i)


VIDEO_CRF_TIP = ("The encoder's quality value (CRF). Lower numbers mean better quality and bigger files. Most "
                 "people use 20 to 28 for HEVC and 26 to 38 for AV1.")


def _spin(lo: int, hi: int, suffix: str = "", tip: str = "") -> QSpinBox:
    s = QSpinBox()
    s.setRange(lo, hi)
    # Size for typical values, not the largest allowed one ("100000 px" would widen the panel).
    s.setMinimumWidth(int(Space.XXL * 3 * text_scale()))
    if suffix:
        s.setSuffix(suffix)
    if tip:
        s.setToolTip(tip)
    return s


def _muted(content: str) -> QLabel:
    return text(content, Type.FOOTNOTE, secondary=True, wrap=True)


class _Form(QFormLayout):
    """Form with one consistent label column width across every section."""

    def addRow(self, label, field=None):  # noqa: N802 - Qt naming
        if isinstance(label, str) and field is not None:
            lab = text(label, Type.BODY)
            lab.setFixedWidth(Size.FORM_LABEL)
            lab.setWordWrap(True)
            lab.setBuddy(field)
            field.setAccessibleName(label)
            return super().addRow(lab, field)
        return super().addRow(label) if field is None else super().addRow(label, field)


def _form(parent: QWidget) -> QFormLayout:
    f = _Form(parent)
    f.setContentsMargins(Space.XS, 0, 0, Space.S)
    f.setHorizontalSpacing(Space.M)
    f.setVerticalSpacing(Space.S)
    f.setLabelAlignment(Qt.AlignLeft | Qt.AlignVCenter)
    f.setFieldGrowthPolicy(QFormLayout.AllNonFixedFieldsGrow)
    f.setRowWrapPolicy(QFormLayout.DontWrapRows)
    return f


def _vbox(parent: QWidget) -> QVBoxLayout:
    v = QVBoxLayout(parent)
    v.setContentsMargins(Space.XS, 0, 0, Space.S)
    v.setSpacing(Space.S)
    return v


class SettingsPanel(QWidget):
    changed = Signal()
    presets_changed = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(Space.L, Space.L, Space.L, Space.L)
        lay.setSpacing(Space.M)
        self.sections: list[Section] = []
        self._applying_preset = False
        self.preset = QComboBox()
        self.preset.setAccessibleName("Preset")
        self.preset.setMinimumContentsLength(8)
        self.preset.setSizeAdjustPolicy(QComboBox.AdjustToMinimumContentsLengthWithIcon)
        self.preset_more = button("", "", "ellipsis", "Save or delete presets")
        self.preset_more.setAccessibleName("Preset actions")
        self.preset_more.setFixedWidth(Size.CONTROL)
        self.preset_menu = QMenu(self)
        self.preset_save = self.preset_menu.addAction("Save Current Options as Preset…", self._preset_save)
        self.preset_delete = self.preset_menu.addAction("Delete Preset", self._preset_delete)
        self.preset_more.clicked.connect(
            lambda: self.preset_menu.exec(self.preset_more.mapToGlobal(self.preset_more.rect().bottomLeft())))
        label = QLabel("Preset")
        label.setBuddy(self.preset)
        row = QWidget()
        h = QHBoxLayout(row)
        h.setContentsMargins(0, 0, 0, 0)
        h.setSpacing(Space.S)
        h.addWidget(label)
        h.addWidget(self.preset, 1)
        h.addWidget(self.preset_more)
        lay.addWidget(row)
        self.preset_note = _muted("")
        lay.addWidget(self.preset_note)
        self.preset.activated.connect(self._preset_chosen)

        # ------------------------------------------------ images
        g = QWidget()
        f = _form(g)
        self.image_format = _combo(
            [("Keep format", "keep"), ("JPEG XL", "jxl"),
             ("WebP", "webp"), ("AVIF", "avif"), ("Don't convert", "none")],
            "Keep format makes each file smaller without changing its type, so it still opens everywhere.\nJPEG XL "
            "makes the smallest files and can turn back into the exact original JPEG, but fewer apps open it.\nWebP "
            "and AVIF open in browsers and most apps.")
        self.jpeg_mode = _combo([("Lossless", "lossless"), ("Lossy", "lossy")],
                                "Lossless: JPEG XL rebuilds the exact original; 'Keep format' re-packs the "
                                "JPEG without touching image quality.")
        self.png_mode = _combo([("Lossless", "lossless"), ("Lossy", "lossy")],
                               "Applies to PNG, GIF, BMP, TIFF and lossless WebP. HEIC, AVIF and lossy WebP are "
                               "already lossy, so they're always re-encoded that way.")
        self.quality = _spin(1, 100, tip="Quality for lossy images. 85-90 is visually near-identical for photos.")
        self.effort = _spin(1, 9, tip="Higher JPEG XL effort makes smaller files more slowly. 7 is a good balance.")
        f.addRow("Convert to", self.image_format)
        from ..compat import jxl_note

        self.jxl_note = _muted(jxl_note())
        f.addRow(self.jxl_note)
        f.addRow("JPEG", self.jpeg_mode)
        f.addRow("Other images", self.png_mode)
        self.png_mode.setToolTip("Applies to PNG, GIF, BMP, TIFF and lossless WebP. HEIC, AVIF and lossy WebP are "
                                 "already lossy, so they're always re-encoded that way.")
        f.addRow("Lossy quality", self.quality)
        self.smart = QCheckBox("Smart quality")
        self.smart.setToolTip("Tries several settings for each file and keeps the smallest one that still measures "
                              "as good enough. It's slower, since each file is encoded more than once.")
        self.smart_target = _combo([("Visually identical", "identical"), ("High", "high"), ("Good", "good")],
                                   "How close to the original the result must measure")
        f.addRow(self.smart)
        f.addRow("Target", self.smart_target)
        self.smart.toggled.connect(self._update_enabled)
        f.addRow("JPEG XL effort", self.effort)
        self.sections.append(Section("Images", g, True))
        lay.addWidget(self.sections[-1])

        # ------------------------------------------------ JPEG output
        g = QWidget()
        f = _form(g)
        self.jpeg_progressive = _combo([("Progressive", True), ("Baseline", False)],
                                       "Progressive JPEGs are smaller and load gradually. Lossless optimization "
                                       "always writes progressive.")
        self.jpeg_subsampling = _combo(
            [("Automatic", "auto"), ("4:4:4", "444"), ("4:2:2", "422"), ("4:2:0", "420")],
            "Automatic keeps the original's chroma subsampling, or uses 4:4:4 at quality 90 and above.")
        f.addRow("Encoding", self.jpeg_progressive)
        f.addRow("Chroma", self.jpeg_subsampling)
        self.jpeg_group = g
        self.sections.append(Section("JPEG output", g, False))
        lay.addWidget(self.sections[-1])

        # ------------------------------------------------ resize
        g = QWidget()
        v = _vbox(g)
        self.resize_mode = _combo([("Don't resize", "none"), ("Limit long edge", "long_edge"),
                                   ("Fit inside a box", "fit"), ("Scale by percent", "percent")],
                                  "Images are never enlarged.")
        self.resize_mode.setAccessibleName("Resize")
        v.addWidget(self.resize_mode)
        self.resize_stack = QStackedWidget()
        self.resize_stack.addWidget(QWidget())
        self.resize_long = _spin(16, 100000, " px")
        self.resize_long.setAccessibleName("Long edge")
        self.resize_stack.addWidget(self._row(QLabel("Long edge"), self.resize_long))
        self.resize_w = _spin(16, 100000, " px")
        self.resize_h = _spin(16, 100000, " px")
        self.resize_w.setAccessibleName("Width")
        self.resize_h.setAccessibleName("Height")
        self.resize_stack.addWidget(self._row(self.resize_w, QLabel("×"), self.resize_h))
        self.resize_pct = _spin(1, 99, " %")
        self.resize_pct.setAccessibleName("Scale to")
        self.resize_stack.addWidget(self._row(QLabel("Scale to"), self.resize_pct))
        v.addWidget(self.resize_stack)
        self.resize_mode.currentIndexChanged.connect(self.resize_stack.setCurrentIndex)
        self.sections.append(Section("Resize images", g, False))
        lay.addWidget(self.sections[-1])

        # ------------------------------------------------ size limit
        g = QWidget()
        v = _vbox(g)
        self.fit_on = QCheckBox("Make each file smaller than")
        self.fit_on.setToolTip("Useful for upload limits on Discord, email or forms. Each file gets the best "
                               "quality that fits, and is only made smaller in size if it has to be. Files already "
                               "under the limit are compressed as usual.")
        self.fit_mb = SizeInput(10 * 1024 ** 2, presets=(1, 5, 8, 10, 25, 50, 100, 500))
        self.fit_mb.setAccessibleName("Size limit")
        self.fit_on.toggled.connect(self.fit_mb.setEnabled)
        self.fit_mb.setEnabled(False)
        v.addWidget(self.fit_on)
        v.addWidget(self._row(self.fit_mb))
        self.sections.append(Section("Size limit", g, False))
        lay.addWidget(self.sections[-1])

        # ------------------------------------------------ metadata
        g = QWidget()
        f = _form(g)
        self.metadata = _combo(METADATA_CHOICES,
                               "Location removes GPS coordinates. Personal info also removes the camera, serial "
                               "numbers, owner, author and comments.\nRotation and color profile always stay, so "
                               "images look the same.")
        f.addRow("Metadata", self.metadata)
        self.image_extras = QCheckBox("Leave out hidden previews and thumbnails")
        self.image_extras.setToolTip("Many cameras store a smaller copy of each photo inside it, and a tiny thumbnail "
                                     "in its details. Viewers make their own. HDR gain maps and other pictures "
                                     "that change how the photo looks are always kept.")
        f.addRow(self.image_extras)
        self.preserve_dates = QCheckBox("Keep original file dates")
        self.preserve_dates.setToolTip("Results get the original's modified and created dates, so photos stay "
                                       "sorted by when they were taken.")
        f.addRow(self.preserve_dates)
        self.sections.append(Section("Privacy", g, True))
        lay.addWidget(self.sections[-1])

        # ------------------------------------------------ videos
        g = QWidget()
        f = _form(g)
        self.video_codec = _combo([("Automatic", "auto"), ("AV1", "av1"), ("HEVC (H.265)", "hevc"),
                                   ("Don't convert", "none")])
        self.video_encoder = _combo([("Automatic", "auto"), ("GPU (NVENC)", "gpu"),
                                     ("CPU", "cpu")])
        self.video_codec.setToolTip("Automatic: HEVC for videos from cameras and phones (AV1 smooths away fine grain "
                                    "and detail in those), AV1 for everything else.\nAV1: smallest files. HEVC: "
                                    "plays on more older devices.")
        self.video_encoder.setToolTip("Automatic uses the graphics card when it can, which is fast, and the processor "
                                      "otherwise.")
        self.video_effort = _combo([("Faster", "medium"), ("Slower, smaller files", "slow")],
                                   "How hard the processor works on each video. Slower makes files about 5% "
                                   "smaller but takes about twice as long. Graphics card encodes aren't affected.")
        self.video_quality = _combo([("Visually identical", "identical"), ("High", "high"),
                                     ("Balanced", "balanced"), ("Smallest", "small"), ("Custom", "custom")],
                                    "Visually identical encodes short test clips, compares them with the original "
                                    "and picks the smallest setting that still looks the same. It's slower, but you "
                                    "don't have to guess.\nCustom lets you set the encoder's quality value "
                                    "yourself.\nEach finished video shows how close it is to the original.")
        self.video_crf = _spin(1, 63, tip=VIDEO_CRF_TIP)
        self.gpu_label = _muted("Checking GPU…")
        f.addRow("Codec", self.video_codec)
        f.addRow("Encoder", self.video_encoder)
        f.addRow("Processor speed", self.video_effort)
        f.addRow("Quality", self.video_quality)
        f.addRow("Quality value", self.video_crf)
        self.video_height = _combo([("Original", 0), ("4K (2160p)", 2160), ("1440p", 1440), ("1080p", 1080),
                                    ("720p", 720), ("480p", 480)],
                                   "Larger videos are scaled down to this. It's measured on the short side, so it "
                                   "works for portrait phone videos too.")
        self.video_fps = _combo([("Original", 0), ("60 fps", 60), ("30 fps", 30), ("24 fps", 24)],
                                "Videos with a higher frame rate are reduced to this.")
        self.audio = _combo([("Copy as-is", 0), ("192 kbps", 192), ("128 kbps", 128), ("96 kbps", 96),
                             ("64 kbps", 64)], "Re-encode audio (AAC in MP4, Opus in MKV/WebM) or keep it unchanged. "
                            "The rate is for stereo: 5.1 gets twice as much, 7.1 about 2.7 times. Tracks "
                            "already at or under it are copied as they are.")
        f.addRow("Max resolution", self.video_height)
        f.addRow("Max frame rate", self.video_fps)
        self.audio.setAccessibleName("Soundtrack")
        f.addRow("Soundtrack", self.audio)
        self.video_tracks = _combo([("Keep all", "all"), ("Only my languages", "mine")],
                                   "Films often come with several dubbed soundtracks and subtitles. This removes "
                                   "the ones in languages you don't use, but always keeps the main soundtrack.")
        self.video_tracks.setAccessibleName("Audio and subtitle tracks")
        f.addRow("Tracks", self.video_tracks)
        self.video_dup_audio = QCheckBox("Leave out lower-quality copies of a soundtrack")
        self.video_dup_audio.setToolTip("Blu-ray films often have the same soundtrack twice: lossless, and a "
                                        "smaller copy for older players. This keeps only the lossless one. "
                                        "Commentaries always stay.")
        f.addRow(self.video_dup_audio)
        self.video_stereo = QCheckBox("Mix surround sound down to stereo")
        self.video_stereo.setToolTip("For watching on laptops, phones and headphones. 5.1 and 7.1 soundtracks "
                                     "become stereo, which takes about a third of the space.")
        f.addRow(self.video_stereo)
        self.video_drop_silent = QCheckBox("Leave out silent soundtracks")
        self.video_drop_silent.setToolTip("Dashcams, drones and screen recordings often have a soundtrack that "
                                          "stays silent from start to end.")
        f.addRow(self.video_drop_silent)
        self.video_dolby_vision = QCheckBox("Include iPhone HDR (Dolby Vision) videos")
        self.video_dolby_vision.setToolTip("These keep an ordinary HDR picture that every player shows, plus a "
                                           "Dolby Vision layer on top. When they're re-encoded, the HDR picture "
                                           "is kept and the extra layer is left out.")
        f.addRow(self.video_dolby_vision)
        self._languages = ""
        self.languages_btn = button("", "", None, "Choose your languages")
        self.languages_btn.setAccessibleName("My languages")
        self.languages_btn.clicked.connect(self._choose_languages)
        f.addRow("Languages", self.languages_btn)
        self._show_languages()
        self.video_cadence = QCheckBox("Remove duplicated frames")
        self.video_cadence.setToolTip("Some videos repeat frames to reach a standard frame rate, like a 24 fps film "
                                      "stored as 30 fps. Removing the repeats gives smoother motion and a smaller "
                                      "file.")
        f.addRow(self.video_cadence)
        self.video_crop = QCheckBox("Remove black bars")
        self.video_crop.setToolTip("Crops black bands around the picture so no space is spent on them. Bars are "
                                   "only removed if they stay black for the whole video.")
        f.addRow(self.video_crop)
        self.video_grain = _combo([("Re-create it", "auto"), ("Re-create it, and hide banding", "banding"),
                                   ("Keep it in the picture", "off")],
                                  "For AV1 on the processor. Grain from film or low light takes a lot of space, so "
                                  "it's removed and a matching grain is added back during playback. Grainy videos "
                                  "get much smaller and look the same. Hiding banding also adds a light grain to "
                                  "clean videos, which breaks up the visible steps in skies and dark scenes.")
        self.video_grain.setAccessibleName("Film grain")
        f.addRow("Film grain", self.video_grain)
        self.video_fonts = QCheckBox("Smaller subtitle fonts")
        self.video_fonts.setToolTip("MKV videos with styled subtitles often carry several large fonts. This keeps "
                                    "only the characters the subtitles use, and leaves out fonts they don't use. "
                                    "Works on AV1 and HEVC videos too, without re-encoding the picture.")
        f.addRow(self.video_fonts)
        self.video_chunked = QCheckBox("Encode scene by scene")
        self.video_chunked.setToolTip("For processor encodes of videos over 90 seconds. Splitting at scene changes "
                                      "lets the parts encode at the same time, which is faster on processors with "
                                      "many cores. With Smart quality, each scene gets the quality it needs, and an "
                                      "interrupted video picks up where it left off.")
        f.addRow(self.video_chunked)
        from .. import quality as _quality

        self.video_metric = _combo([(_quality.VIDEO_METRIC_LABELS[k], k) for k in _quality.VIDEO_METRICS],
                                   "How Smart quality and Visually identical judge whether a video still looks "
                                   "right. One that isn't available here is replaced by VMAF.")
        self.video_metric.setAccessibleName("Video quality measure")
        f.addRow("Quality measure", self.video_metric)
        f.addRow(_described(self.video_metric, _quality.VIDEO_METRIC_HELP))
        f.addRow(self.gpu_label)
        self.sections.append(Section("Videos", g, True))
        lay.addWidget(self.sections[-1])

        # ------------------------------------------------ music and other audio
        g = QWidget()
        f = _form(g)
        self.audio_lossless = _combo([("FLAC (lossless)", "flac"), ("Lossy format", "lossy"),
                                      ("Don't change", "keep")],
                                     "Applies to WAV, AIFF, ALAC, APE, WavPack and FLAC files. FLAC keeps every "
                                     "sample at about half the size of WAV.")
        self.audio_lossy = _combo([("Keep as they are", "keep"), ("Re-encode high bitrates", "reencode")],
                                  "MP3, AAC, Vorbis, WMA and Opus files. Re-encoding only happens when the file's "
                                  "bitrate is well above the target, since every lossy step costs some quality.")
        self.audio_format = _combo([("Opus (smallest)", "opus"), ("AAC (Apple devices)", "aac"),
                                    ("MP3 (plays everywhere)", "mp3")],
                                   "Opus sounds best for its size. AAC and MP3 play on more older devices.")
        self.audio_quality = _combo([("High", "high"), ("Balanced", "balanced"), ("Smallest", "small")],
                                    "Opus 160/128/96 kbps, AAC 256/192/128 kbps, MP3 VBR V0/V2/V5 (stereo).")
        self.audio_mono = QCheckBox("Mono when channels match")
        self.audio_mono.setToolTip("Stereo files with identical left and right channels are saved as mono. That "
                                   "halves the size and sounds the same.")
        self.audio_speech = QCheckBox("Smaller podcasts and audiobooks")
        self.audio_speech.setToolTip("Recordings of talk over 10 minutes are saved as mono at a lower bitrate, "
                                     "which is plenty for voices. Music is never affected.")
        self.audio_fake_hires = QCheckBox("Undo upsampled hi-res")
        self.audio_fake_hires.setToolTip("Some 88.2 to 192 kHz files are CD audio made bigger: nothing is there above "
                                         "what 44.1 or 48 kHz holds. Those go back to that rate; real hi-res stays.")
        self.audio_cd = QCheckBox("Reduce hi-res to CD quality")
        self.audio_cd.setToolTip("Converts 96 or 192 kHz and 24-bit audio to 44.1 or 48 kHz and 16-bit. The "
                                 "difference is beyond what people can hear.")
        f.addRow("Lossless files", self.audio_lossless)
        f.addRow("Compressed files", self.audio_lossy)
        f.addRow("Lossy format", self.audio_format)
        f.addRow("Quality", self.audio_quality)
        f.addRow(self.audio_mono)
        f.addRow(self.audio_speech)
        f.addRow(self.audio_fake_hires)
        f.addRow(self.audio_cd)
        self.sections.append(Section("Audio", g, False))
        lay.addWidget(self.sections[-1])

        # ------------------------------------------------ documents
        g = QWidget()
        f = _form(g)
        self.documents = _combo([("Shrink, identical pictures", "lossless"), ("Also reduce large pictures", "reduce"),
                                 ("Don't change", "none")],
                                "Word, PowerPoint, Excel and OpenDocument files and PDFs are mostly pictures. "
                                "They're stored smaller with exactly the same pixels; everything else in the "
                                "document stays byte-for-byte the same (checked).\nReduce: pictures larger than "
                                "2560 px are also scaled down: much smaller, still sharp on screen.")
        self.documents.setAccessibleName("Documents")
        f.addRow("Pictures", self.documents)
        self.document_fonts = QCheckBox("Cut embedded fonts to the characters used")
        self.document_fonts.setToolTip("Word, PowerPoint and PDF files often carry whole fonts for a few hundred "
                                       "characters. The document looks exactly the same; typing new characters "
                                       "on a computer without the font may show another font.")
        f.addRow(self.document_fonts)
        self.document_drop_editing = QCheckBox("Leave out Illustrator and Photoshop editing data")
        self.document_drop_editing.setToolTip("PDFs saved with \"Preserve Illustrator Editing Capabilities\" carry "
                                              "a whole copy of the Illustrator file, often doubling their size. The "
                                              "PDF looks the same without it, but Illustrator then opens it as a "
                                              "plain PDF.")
        f.addRow(self.document_drop_editing)
        f.addRow(_muted("Signed and password-protected documents are left alone."))
        self.sections.append(Section("Documents", g, False))
        lay.addWidget(self.sections[-1])

        # ------------------------------------------------ output
        g = QWidget()
        v = _vbox(g)
        self.output_mode = _combo(OUTPUT_CHOICES)
        self.output_mode.setAccessibleName("Where results go")
        self.output_mode.setToolTip(f"Replace moves originals to the {BIN_NAME} once each result is checked.\n"
                                    "Next to originals saves the result beside the original, with its new file "
                                    "type.\nNew name saves the result beside the original under a new name.\n"
                                    "Another folder saves results somewhere else and leaves the originals alone.")
        v.addWidget(self.output_mode)
        self.output_stack = QStackedWidget()
        self.allow_delete = QCheckBox(f"Delete permanently on drives\nwithout a {BIN_NAME}")
        self.allow_delete.setToolTip("USB sticks and network drives have no Recycle Bin. With this on, originals "
                                     "there are deleted for good once the result is checked.")
        replace_box = QWidget()
        rl = QVBoxLayout(replace_box)
        rl.setContentsMargins(0, 0, 0, 0)
        rl.addWidget(self.allow_delete)
        self.output_stack.addWidget(replace_box)
        self.output_stack.addWidget(QWidget())  # next to originals: nothing to set
        self.suffix = QLineEdit()
        self.suffix.setToolTip(NAME_TIP)
        self.suffix.setAccessibleName("New name")
        self.suffix.setPlaceholderText("_compressed or %name% %date%")
        self.output_stack.addWidget(self._row(QLabel("Name"), self.suffix))
        folder_box = QWidget()
        fl = QVBoxLayout(folder_box)
        fl.setContentsMargins(0, 0, 0, 0)
        self.output_dir = QLineEdit()
        self.output_dir.setPlaceholderText("Output folder")
        self.output_dir.setAccessibleName("Output folder")
        browse = QPushButton("…")
        browse.setAccessibleName("Choose the output folder")
        browse.setToolTip("Choose the output folder")
        browse.setFixedWidth(32)
        browse.clicked.connect(self._browse_output)
        fl.addWidget(self._row(self.output_dir, browse))
        self.keep_structure = QCheckBox("Recreate subfolders")
        fl.addWidget(self.keep_structure)
        self.output_stack.addWidget(folder_box)
        v.addWidget(self.output_stack)
        self.output_mode.currentIndexChanged.connect(self.output_stack.setCurrentIndex)
        # Only keep a result when it saved enough
        self.require_savings = QCheckBox("Only keep results that save at least")
        self.require_savings.setToolTip("Otherwise the original stays exactly as it is and the result is thrown away.")
        self.min_savings = _spin(0, 90, " %", "Minimum saving, as a percentage of the original")
        self.min_savings.setAccessibleName("Minimum saving in percent")
        self.min_savings_kb = _spin(0, 1_000_000, " KB", "…and at least this much space (0 = no size minimum)")
        self.min_savings_kb.setAccessibleName("Minimum saving in kilobytes")
        v.addWidget(self.require_savings)
        v.addWidget(self._row(self.min_savings, QLabel("and"), self.min_savings_kb))
        self.require_savings.toggled.connect(lambda on: (self.min_savings.setEnabled(on),
                                                         self.min_savings_kb.setEnabled(on)))
        self.sections.append(Section("Output", g, True))
        lay.addWidget(self.sections[-1])

        # ------------------------------------------------ rules
        g = QWidget()
        f = _form(g)
        self.min_image_kb = _spin(0, 100000, " KB")
        self.min_video_mb = _spin(0, 100000, " MB")
        self.skip_programs = QCheckBox("Skip program folders")
        self.skip_programs.setToolTip("Folders containing programs or games are skipped, since converting their "
                                      "images would break them.")
        self.include_hidden = QCheckBox("Include hidden items")
        f.addRow("Skip images under", self.min_image_kb)
        f.addRow("Skip videos under", self.min_video_mb)
        self.min_audio_kb = _spin(0, 100000, " KB")
        f.addRow("Skip audio under", self.min_audio_kb)
        f.addRow(self.skip_programs)
        f.addRow(self.include_hidden)
        self.sections.append(Section("Rules", g, False))
        lay.addWidget(self.sections[-1])

        # ------------------------------------------------ performance
        g = QWidget()
        f = _form(g)
        self.workers = _spin(0, 64, tip="How many images are compressed at once. 0 = automatic.")
        self.workers.setSpecialValueText("Automatic")
        self.low_priority = QCheckBox("Low priority")
        self.low_priority.setToolTip("Encoders run at low priority so the computer stays responsive")
        f.addRow("Parallel images", self.workers)
        self.video_jobs = _combo([("Automatic", 0), ("One at a time", 1), ("Two at once", 2)],
                                 "Automatic encodes two at once on NVIDIA RTX 40-series and newer graphics cards, "
                                 "which have two video encoders. Otherwise it's one at a time, since processor "
                                 "encoders already use every core.")
        f.addRow("Videos at once", self.video_jobs)
        f.addRow(self.low_priority)
        self.sections.append(Section("Performance", g, False))
        lay.addWidget(self.sections[-1])

        lay.addWidget(_muted("Every file is verified before anything is replaced. Everything can be undone later."))
        lay.addStretch(1)

        for w in (self.image_format, self.jpeg_mode, self.png_mode, self.video_codec, self.output_mode,
                  self.audio_lossless, self.audio_lossy, self.video_tracks, self.video_quality):
            w.currentIndexChanged.connect(self._update_enabled)
        for sig in self._signals():
            sig.connect(lambda *_: self.changed.emit())
        self.changed.connect(lambda: None if self._applying_preset else self.refresh_presets())
        self._update_enabled()

    @staticmethod
    def _row(*widgets) -> QWidget:
        w = QWidget()
        h = QHBoxLayout(w)
        h.setContentsMargins(0, 0, 0, 0)
        for x in widgets:
            h.addWidget(x, 1 if isinstance(x, (QLineEdit, QSpinBox)) else 0)
        return w

    def _show_languages(self) -> None:
        from .. import languages

        keys = languages.parse(self._languages)
        label = languages.describe(keys or languages.detect())
        self.languages_btn.setText(label if keys else f"{label} (this PC)")
        self.languages_btn.setToolTip("Tracks in these languages are kept. Click to choose.")

    def _choose_languages(self) -> None:
        from .. import languages
        from .language_dialog import LanguageDialog

        dlg = LanguageDialog(languages.parse(self._languages) or languages.detect(), self)
        if dlg.exec():
            chosen = dlg.chosen()
            # Saved only when it differs from what this computer suggests (so it keeps following the PC).
            self._languages = "" if chosen == dlg.detected else ",".join(chosen)
            self._show_languages()
            self.changed.emit()

    def _browse_output(self) -> None:
        d = QFileDialog.getExistingDirectory(self, "Output folder", self.output_dir.text())
        if d:
            self.output_dir.setText(os.path.normpath(d))

    def _signals(self):
        combos = (self.image_format, self.jpeg_mode, self.png_mode, self.jpeg_progressive, self.jpeg_subsampling,
                  self.resize_mode, self.metadata, self.video_codec, self.video_encoder, self.video_effort,
                  self.video_quality,
                  self.output_mode, self.smart_target, self.video_height, self.video_fps, self.audio,
                  self.audio_lossless, self.audio_lossy, self.audio_format, self.audio_quality, self.video_jobs,
                  self.video_tracks, self.documents, self.video_metric, self.video_grain)
        spins = (self.quality, self.effort, self.resize_long, self.resize_w, self.resize_h, self.resize_pct,
                 self.min_savings, self.min_savings_kb, self.min_image_kb, self.min_video_mb, self.workers,
                 self.min_audio_kb, self.video_crf)
        checks = (self.allow_delete, self.keep_structure, self.skip_programs, self.include_hidden,
                  self.low_priority, self.preserve_dates, self.require_savings, self.smart, self.audio_mono, self.audio_speech,
                  self.audio_fake_hires,
                  self.audio_cd, self.video_cadence, self.fit_on, self.video_crop, self.video_fonts,
                  self.video_dup_audio, self.video_stereo, self.document_fonts,
                  self.video_drop_silent, self.video_dolby_vision, self.image_extras,
                  self.document_drop_editing,
                  self.video_chunked)
        return ([c.currentIndexChanged for c in combos] + [s.valueChanged for s in spins]
                + [c.toggled for c in checks] + [self.suffix.textChanged, self.output_dir.textChanged,
                                                 self.fit_mb.changed])

    def set_file_kinds(self, kinds: set[str] | None) -> None:
        """Show only the options for the kinds of files in the list ("image"/"video"/"audio"; None = all)."""
        self._kinds = kinds
        self._update_enabled()

    @staticmethod
    def _show(w: QWidget, on: bool) -> None:
        """Show/hide a control together with its form label."""
        lay = w.parentWidget().layout() if w.parentWidget() is not None else None
        if isinstance(lay, QFormLayout) and lay.indexOf(w) >= 0:
            lay.setRowVisible(w, on)
        else:
            w.setVisible(on)

    def _update_enabled(self) -> None:
        kinds = getattr(self, "_kinds", None)
        has = (lambda k: True) if not kinds else (lambda k: k in kinds)
        show = self._show
        sec = {x.toggle.text(): x for x in self.sections}

        fmt = self.image_format.currentData()
        images = fmt != "none"
        smart = self.smart.isChecked()
        for w in (self.jpeg_mode, self.png_mode, self.smart):
            show(w, images)
        show(self.quality, images and not smart)
        show(self.smart_target, images and smart)
        show(self.effort, fmt == "jxl")
        show(self.jxl_note, fmt == "jxl")
        sec["Images"].setVisible(has("image"))
        sec["JPEG output"].setVisible(has("image") and fmt == "keep")
        sec["Resize images"].setVisible(has("image") and images)

        videos = self.video_codec.currentData() != "none"
        show(self.video_effort, videos and self.video_encoder.currentData() != "gpu")
        for w in (self.video_encoder, self.video_quality, self.video_height, self.video_fps, self.audio,
                  self.video_cadence, self.gpu_label, self.video_tracks):
            show(w, videos)
        show(self.languages_btn, videos and self.video_tracks.currentData() == "mine")
        show(self.video_crf, videos and self.video_quality.currentData() == "custom")
        sec["Videos"].setVisible(has("video"))

        lossless, lossy = self.audio_lossless.currentData(), self.audio_lossy.currentData()
        uses_lossy = lossless == "lossy" or lossy == "reencode"
        show(self.audio_format, uses_lossy)
        show(self.audio_quality, uses_lossy)
        show(self.audio_mono, lossless != "keep" or lossy != "keep")
        show(self.audio_speech, uses_lossy)
        show(self.audio_cd, lossless == "flac")
        show(self.audio_fake_hires, lossless == "flac" and not self.audio_cd.isChecked())
        sec["Audio"].setVisible(has("audio"))
        sec["Documents"].setVisible(has("document"))

        show(self.min_image_kb, has("image") and images)
        show(self.min_video_mb, has("video") and videos)
        show(self.min_audio_kb, has("audio"))
        show(self.workers, has("image") or has("audio"))
        show(self.video_jobs, has("video") and videos)

    def load(self, s: Settings) -> None:
        _set_combo(self.image_format, s.image_format)
        _set_combo(self.jpeg_mode, s.jpeg_mode)
        _set_combo(self.png_mode, s.png_mode)
        self.quality.setValue(s.image_quality)
        self.effort.setValue(s.image_effort)
        _set_combo(self.jpeg_progressive, s.jpeg_progressive)
        _set_combo(self.jpeg_subsampling, s.jpeg_subsampling)
        _set_combo(self.resize_mode, s.resize_mode)
        self.resize_stack.setCurrentIndex(self.resize_mode.currentIndex())
        self.resize_long.setValue(s.resize_long_edge)
        self.resize_w.setValue(s.resize_width)
        self.resize_h.setValue(s.resize_height)
        self.resize_pct.setValue(s.resize_percent)
        self.fit_on.setChecked(s.fit_under_kb > 0)
        if s.fit_under_kb > 0:
            self.fit_mb.set_bytes(s.fit_under_kb * 1024)
        _set_combo(self.metadata, s.metadata)
        _set_combo(self.video_codec, s.video_codec)
        _set_combo(self.video_encoder, s.video_encoder)
        _set_combo(self.video_effort, s.video_effort)
        _set_combo(self.video_quality, s.video_quality)
        self.video_crf.setValue(s.video_crf)
        _set_combo(self.video_tracks, s.video_tracks)
        self._languages = s.languages
        self._show_languages()
        _set_combo(self.output_mode, s.output_mode)
        self.output_stack.setCurrentIndex(self.output_mode.currentIndex())
        self.suffix.setText(s.output_suffix)
        self.output_dir.setText(s.output_dir)
        self.keep_structure.setChecked(s.keep_structure)
        self.allow_delete.setChecked(s.allow_permanent_delete)
        self.min_savings.setValue(int(s.min_savings_pct))
        self.min_savings_kb.setValue(s.min_savings_kb)
        self.require_savings.setChecked(s.require_savings)
        self.min_savings.setEnabled(s.require_savings)
        self.min_savings_kb.setEnabled(s.require_savings)
        self.preserve_dates.setChecked(s.preserve_dates)
        self.smart.setChecked(s.smart_quality)
        _set_combo(self.smart_target, s.smart_target)
        _set_combo(self.video_height, s.video_max_height)
        _set_combo(self.video_fps, s.video_max_fps)
        _set_combo(self.audio, s.audio_bitrate)
        _set_combo(self.audio_lossless, s.audio_lossless)
        _set_combo(self.audio_lossy, s.audio_lossy)
        _set_combo(self.audio_format, s.audio_format)
        _set_combo(self.audio_quality, s.audio_quality)
        _set_combo(self.documents, s.documents)
        self.document_fonts.setChecked(s.document_fonts)
        self.image_extras.setChecked(s.image_extras)
        self.document_drop_editing.setChecked(s.document_drop_editing)
        self.audio_mono.setChecked(s.audio_mono)
        self.audio_speech.setChecked(s.audio_speech)
        self.audio_fake_hires.setChecked(s.audio_fake_hires)
        self.audio_cd.setChecked(s.audio_cd_quality)
        self.video_cadence.setChecked(s.video_fix_cadence)
        self.video_crop.setChecked(s.video_crop_bars)
        _set_combo(self.video_grain, s.video_grain)
        self.video_fonts.setChecked(s.video_fonts)
        self.video_dup_audio.setChecked(s.video_dup_audio)
        self.video_stereo.setChecked(s.video_stereo)
        self.video_drop_silent.setChecked(s.video_drop_silent)
        self.video_dolby_vision.setChecked(s.video_dolby_vision)
        self.video_chunked.setChecked(s.video_chunked)
        _set_combo(self.video_metric, s.video_metric)
        _set_combo(self.video_jobs, s.video_jobs)
        self.min_audio_kb.setValue(s.min_audio_kb)
        self.refresh_presets()
        self.min_image_kb.setValue(s.min_image_kb)
        self.min_video_mb.setValue(s.min_video_mb)
        self.skip_programs.setChecked(s.skip_program_folders)
        self.include_hidden.setChecked(s.include_hidden)
        self.workers.setValue(s.workers)
        self.low_priority.setChecked(s.low_priority)
        self._update_enabled()

    def settings(self) -> Settings:
        s = Settings(
            image_format=self.image_format.currentData(), jpeg_mode=self.jpeg_mode.currentData(),
            png_mode=self.png_mode.currentData(), image_quality=self.quality.value(),
            image_effort=self.effort.value(), jpeg_progressive=bool(self.jpeg_progressive.currentData()),
            jpeg_subsampling=self.jpeg_subsampling.currentData(), resize_mode=self.resize_mode.currentData(),
            resize_long_edge=self.resize_long.value(), resize_width=self.resize_w.value(),
            resize_height=self.resize_h.value(), resize_percent=self.resize_pct.value(),
            metadata=self.metadata.currentData(),
            video_codec=self.video_codec.currentData(), video_encoder=self.video_encoder.currentData(),
            video_effort=self.video_effort.currentData(),
            video_quality=self.video_quality.currentData(), video_crf=self.video_crf.value(),
            video_tracks=self.video_tracks.currentData(),
            languages=self._languages,
            output_mode=self.output_mode.currentData(), output_suffix=self.suffix.text(),
            output_dir=self.output_dir.text().strip(), keep_structure=self.keep_structure.isChecked(),
            allow_permanent_delete=self.allow_delete.isChecked(),
            min_savings_pct=float(self.min_savings.value()), min_image_kb=self.min_image_kb.value(),
            min_savings_kb=self.min_savings_kb.value(), require_savings=self.require_savings.isChecked(),
            preserve_dates=self.preserve_dates.isChecked(), smart_quality=self.smart.isChecked(),
            smart_target=self.smart_target.currentData(), video_max_height=int(self.video_height.currentData()),
            video_max_fps=int(self.video_fps.currentData()), audio_bitrate=int(self.audio.currentData()),
            min_video_mb=self.min_video_mb.value(), skip_program_folders=self.skip_programs.isChecked(),
            include_hidden=self.include_hidden.isChecked(), workers=self.workers.value(),
            low_priority=self.low_priority.isChecked(),
            fit_under_kb=max(1, self.fit_mb.bytes() // 1024) if self.fit_on.isChecked() else 0,
            audio_lossless=self.audio_lossless.currentData(), audio_lossy=self.audio_lossy.currentData(),
            audio_format=self.audio_format.currentData(), audio_quality=self.audio_quality.currentData(),
            audio_mono=self.audio_mono.isChecked(), audio_speech=self.audio_speech.isChecked(),
            audio_fake_hires=self.audio_fake_hires.isChecked(),
            audio_cd_quality=self.audio_cd.isChecked(),
            min_audio_kb=self.min_audio_kb.value(), video_fix_cadence=self.video_cadence.isChecked(),
            video_crop_bars=self.video_crop.isChecked(),
            video_grain=self.video_grain.currentData(),
            video_fonts=self.video_fonts.isChecked(), video_dup_audio=self.video_dup_audio.isChecked(),
            video_stereo=self.video_stereo.isChecked(), video_drop_silent=self.video_drop_silent.isChecked(),
            video_dolby_vision=self.video_dolby_vision.isChecked(), video_chunked=self.video_chunked.isChecked(),
            video_metric=self.video_metric.currentData(),
            video_jobs=int(self.video_jobs.currentData()), documents=self.documents.currentData(),
            document_fonts=self.document_fonts.isChecked(), image_extras=self.image_extras.isChecked(),
            document_drop_editing=self.document_drop_editing.isChecked(),
        )
        s.validate()
        return s

    # ---- presets
    def refresh_presets(self) -> None:
        from .. import presets

        current = presets.matching(self.settings()) if hasattr(self, "audio") else None
        self.preset.blockSignals(True)
        self.preset.clear()
        self.preset.addItem("Custom", "")
        for name in presets.names():
            self.preset.addItem(name, name)
            self.preset.setItemData(self.preset.count() - 1, presets.description(name), Qt.ToolTipRole)
        self.preset.setCurrentIndex(max(0, self.preset.findData(current or "")))
        self.preset.blockSignals(False)
        deletable = bool(current) and not presets.is_builtin(current or "")
        self.preset_delete.setEnabled(deletable)
        self.preset_delete.setText(f"Delete “{current}”" if deletable else "Delete Preset")
        self.preset_note.setText(presets.description(current) if current else "")
        self.preset_note.setVisible(bool(current))

    def _preset_chosen(self, _index: int) -> None:
        name = self.preset.currentData()
        if name:
            self.apply_preset(name)

    def apply_preset(self, name: str) -> bool:
        """Load a preset's options into the panel. False if there is no such preset."""
        from .. import presets

        if presets.get(name) is None:
            return False
        self._applying_preset = True
        try:
            self.load(presets.apply(name, self.settings()))
        finally:
            self._applying_preset = False
        self.changed.emit()
        return True

    def _preset_save(self) -> None:
        from PySide6.QtWidgets import QInputDialog

        from .. import presets
        from .dialogs import inform

        name, ok = QInputDialog.getText(self, "Save preset", "Name for these options:")
        if not ok or not name.strip():
            return
        try:
            presets.save(name, self.settings())
        except ValueError as e:
            inform(self, "Couldn't save the preset", str(e))
            return
        self.refresh_presets()
        self.presets_changed.emit()

    def _preset_delete(self) -> None:
        from .. import presets
        from .dialogs import ask

        name = self.preset.currentData()
        if name and not presets.is_builtin(name) and ask(self, f"Delete the preset “{name}”?", "", "Delete Preset",
                                                          destructive=True):
            presets.delete(name)
            self.refresh_presets()
            self.presets_changed.emit()

    def show_encoders(self, enc: dict) -> None:
        from ..video import GPU_ENCODERS

        def maker(encoder: str) -> str:
            return {"nvenc": "NVIDIA", "qsv": "Intel", "amf": "AMD"}[encoder.rsplit("_", 1)[1]]

        av1 = next((e for e in GPU_ENCODERS["av1"] if enc.get(e)), None)
        hevc = next((e for e in GPU_ENCODERS["hevc"] if enc.get(e)), None)
        if av1 and hevc:
            text = f"{maker(av1)} GPU found: AV1 and HEVC encode on the GPU (fast)."
        elif av1:
            text = f"{maker(av1)} GPU found: AV1 on the GPU (fast); HEVC uses the CPU."
        elif hevc:
            text = f"{maker(hevc)} GPU found: HEVC on the GPU; AV1 needs a newer GPU, so it uses the CPU (slow)."
        elif enc:
            text = "No supported graphics card found, so videos are encoded on the processor, which is slower."
        else:
            text = "Video conversion isn't available because FFmpeg is missing."
        self.gpu_label.setText(text)
