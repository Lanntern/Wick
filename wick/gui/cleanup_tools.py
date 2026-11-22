"""The Clean Up tools: what each one looks for, its options, and how its results are grouped."""

from __future__ import annotations

from typing import cast

import os
from pathlib import Path

from PySide6.QtWidgets import QComboBox, QFileDialog, QSpinBox

from ..cleanup import (
    actions, appcompress, archives, backups, broken, caches, devclean, duplicates, empties, extensions, folders,
    installers, largefiles, music, pairs, proxies, similar, takeout, videos, winclean,
)
from ..cleanup.common import Candidate
from ..fileops import BIN_NAME
from ..cleanup import apps as apps_mod
from .cleanup_section import Tool, ToolSection, _existing
from .widgets import SizeInput, button, dialog_buttons, dialog_layout

# ---------------------------------------------------------------- tool definitions

def _keep_options(sec: ToolSection):
    keep = QComboBox()
    for label, value in (("Oldest copy", "oldest"), ("Newest copy", "newest"), ("Shortest path", "shortest_path"),
                         ("Copy in a chosen folder…", "folder")):
        keep.addItem(label, value)
    keep.setCurrentIndex(max(0, keep.findData(sec.qs.value("cleanup/duplicates/keep", "oldest", str))))
    keep.setToolTip("Which copy stays when files are identical")

    def changed():
        sec.qs.setValue("cleanup/duplicates/keep", keep.currentData())
        if keep.currentData() == "folder":
            d = QFileDialog.getExistingDirectory(sec, "Keep copies inside this folder", str(Path.home()))
            if d:
                sec.qs.setValue("cleanup/duplicates/prefer", d)
                keep.setToolTip(f"Keep the copy inside {d}")
            else:
                keep.setCurrentIndex(0)
    keep.currentIndexChanged.connect(changed)
    sec.extra_keep = keep
    sec.extra_min = _size_setting(sec, "cleanup/duplicates", 100 * 1024)
    return [("Keep", keep), ("Ignore files under", sec.extra_min)]


def _size_setting(sec: "ToolSection", key: str, default: int,
                  old_unit: tuple[str, int] = ("min_kb", 1024)) -> SizeInput:
    """A size option, remembered in bytes (earlier versions kept a whole number of KB or MB)."""
    old = sec.qs.value(f"{key}/{old_unit[0]}", None)
    start = int(str(old)) * old_unit[1] if old is not None else default
    w = SizeInput(cast(int, sec.qs.value(f"{key}/min_bytes", start, int)))
    w.changed.connect(lambda b: sec.qs.setValue(f"{key}/min_bytes", b))
    return w


def _scan_duplicates(sec: ToolSection, progress, cancel):
    prefer = cast(str, sec.qs.value("cleanup/duplicates/prefer", "", str))
    return duplicates.find_duplicates(sec.folders, min_size=sec.extra_min.bytes(),
                                      keep=sec.extra_keep.currentData(), prefer=Path(prefer) if prefer else None,
                                      progress=progress, cancel=cancel)


def _similar_options(sec: "ToolSection"):
    how = QComboBox()
    for label, value in (("Nearly identical", "very"), ("Similar", "similar")):
        how.addItem(label, value)
    how.setCurrentIndex(max(0, how.findData(sec.qs.value("cleanup/similar/strictness", "very", str))))
    how.setToolTip("Nearly identical finds re-saved, resized or slightly edited copies. Similar also finds burst "
                   "shots.")
    how.currentIndexChanged.connect(lambda: sec.qs.setValue("cleanup/similar/strictness", how.currentData()))
    sec.extra_how = how
    sec.extra_min = _size_setting(sec, "cleanup/similar", 50 * 1024)
    return [("Match", how), ("Ignore photos under", sec.extra_min)]


def _large_options(sec: "ToolSection"):
    sec.extra_min = _size_setting(sec, "cleanup/large", 500 * 1024 ** 2, old_unit=("min_mb", 1024 ** 2))
    kind = QComboBox()
    for value, label in largefiles.KINDS.items():
        kind.addItem(label, value)
    kind.setCurrentIndex(max(0, kind.findData(sec.qs.value("cleanup/large/kind", "all", str))))
    kind.currentIndexChanged.connect(lambda: sec.qs.setValue("cleanup/large/kind", kind.currentData()))
    kind.setAccessibleName("Type of file")
    sec.extra_how = kind
    return [("Larger than", sec.extra_min), ("Type", kind)]


def _scan_large(sec: "ToolSection", progress, cancel):
    return largefiles.find_large_files(sec.folders, sec.extra_min.bytes() / 1024 ** 2, sec.extra_how.currentData(), progress,
                                       cancel)


def _scan_similar(sec: "ToolSection", progress, cancel):
    return similar.find_similar(sec.folders, strictness=sec.extra_how.currentData(),
                                min_size=sec.extra_min.bytes(), progress=progress, cancel=cancel)


def _videos_options(sec: "ToolSection"):
    how = QComboBox()
    for label, value in (("Nearly identical", "very"), ("Similar", "similar")):
        how.addItem(label, value)
    how.setCurrentIndex(max(0, how.findData(sec.qs.value("cleanup/videos/strictness", "very", str))))
    how.setToolTip("Nearly identical finds the same video in another size or format. Similar also finds copies with "
                   "changed colors, a watermark or burned-in subtitles.")
    how.currentIndexChanged.connect(lambda: sec.qs.setValue("cleanup/videos/strictness", how.currentData()))
    sec.extra_how = how
    return [("Match", how)]


def _music_options(sec: "ToolSection"):
    how = QComboBox()
    for label, value in (("Same recording", "sound"), ("Same title and artist", "tags")):
        how.addItem(label, value)
    how.setCurrentIndex(max(0, how.findData(sec.qs.value("cleanup/music/by", "sound", str))))
    how.setToolTip("Same recording listens to the songs, whatever their names and tags. Same title and artist only "
                   "reads the tags, which is faster.")
    how.currentIndexChanged.connect(lambda: sec.qs.setValue("cleanup/music/by", how.currentData()))
    sec.extra_how = how
    return [("Match", how)]


def _scan_music(sec: "ToolSection", progress, cancel):
    phone = cast(str, sec.qs.value("mirror/dest", "", str))      # (the phone copy is meant to repeat the library)
    return music.find_same_music(sec.folders, by=sec.extra_how.currentData(), exclude=[Path(phone)] if phone else [],
                                 progress=progress, cancel=cancel)


def _broken_options(sec: "ToolSection"):
    from PySide6.QtWidgets import QCheckBox

    box = QCheckBox("Play videos and music all the way through")
    box.setToolTip("Finds damage anywhere in a video or song, not just a missing end. Takes about as long as "
                   "converting them.")
    box.setChecked(cast(bool, sec.qs.value("cleanup/broken/thorough", False, bool)))
    box.toggled.connect(lambda on: sec.qs.setValue("cleanup/broken/thorough", on))
    sec.extra_thorough = box
    return [("", box)]


def _pairs_options(sec: "ToolSection"):
    combo = QComboBox()
    combo.addItem("List the JPEGs (keep the RAW files)", "raw")
    combo.addItem("List the RAW files (keep the JPEGs)", "jpeg")
    combo.setCurrentIndex(max(0, combo.findData(sec.qs.value("cleanup/pairs/keep", "raw", str))))
    combo.setAccessibleName("Which of each pair to list")
    combo.currentIndexChanged.connect(lambda _i: sec.qs.setValue("cleanup/pairs/keep", combo.currentData()))
    sec.extra_keep = combo
    return [("Of each pair", combo)]


def _similar_group_title(g: str, items: list[Candidate]) -> str:
    return f"{len(items) + 1} similar photos: keeping {items[0].keeper.name}"


def _days_option(key: str, default: int, label: str, tip: str):
    def make(sec: ToolSection):
        spin = QSpinBox()
        spin.setRange(1, 3650)
        spin.setSuffix(" days")
        spin.setValue(cast(int, sec.qs.value(f"cleanup/{key}/days", default, int)))
        spin.setToolTip(tip)
        spin.valueChanged.connect(lambda v: sec.qs.setValue(f"cleanup/{key}/days", v))
        sec.extra_days = spin
        return [(label, spin)]
    return make


def _appcompress_options(sec: ToolSection):
    from PySide6.QtWidgets import QCheckBox, QDialog, QWidget
    from .dialogs import inform
    from .widgets import hbox
    import json

    from .app_controls import FileTypePicker, algorithm_picker

    group, algorithm = algorithm_picker(sec.qs.value('appcompress/algorithm', appcompress.DEFAULT_ALGORITHM, str))
    algorithm.setAccessibleName('App and game compression algorithm')
    algorithm.currentIndexChanged.connect(lambda: sec.qs.setValue('appcompress/algorithm', algorithm.currentData()))
    skip = FileTypePicker(str(sec.qs.value('appcompress/skip', appcompress.DEFAULT_SKIP, str)))
    skip.changed.connect(lambda: sec.qs.setValue('appcompress/skip', ','.join(skip.values())))
    explanation = ('Updates replace some compressed files with new, uncompressed copies. While Wick is open, '
                   'it watches for file changes and also checks every 15 minutes, compressing only new, uncompressed files, '
                   'using the saved algorithm and exclusions. Running games, DirectStorage warnings and '
                   'folders requiring administrator permission are skipped. Local History must be enabled to '
                   'remember tracked folders. Undo is available there too.')
    box = QCheckBox('Compress again after game updates')
    box.setToolTip(explanation)
    box.setChecked(cast(bool, sec.qs.value('appcompress/recompress', True, bool)))
    box.toggled.connect(lambda on: sec.qs.setValue('appcompress/recompress', on))
    help_button = button('?', '', None, explanation)
    help_button.setAccessibleName('Explain compression after game updates')
    help_button.clicked.connect(lambda: inform(sec, 'Why compress again after updates?', explanation))
    watch = QWidget()
    watch.setLayout(hbox(box, help_button, None))
    idle = QCheckBox('Wait for 5 minutes without input')
    idle.setChecked(cast(bool, sec.qs.value('appcompress/idle_only', True, bool)))
    idle.setToolTip('Waits for 5 minutes without keyboard or mouse input to reduce interference with other work. '
                    'Running games and battery policy are checked too.')
    idle.toggled.connect(lambda on: sec.qs.setValue('appcompress/idle_only', on))
    tray = QCheckBox('Keep watching in the tray after closing')
    tray.setChecked(cast(bool, sec.qs.value('appcompress/tray', False, bool)))
    tray.setToolTip('Wick stays running in the notification area. Use Quit in the tray menu to stop watching.')
    tray.toggled.connect(lambda on: sec.qs.setValue('appcompress/tray', on))
    sec.app_algorithm, sec.app_skip = algorithm, skip
    samples = QCheckBox('Test temporary samples for estimates (slower)')
    samples.setChecked(cast(bool, sec.qs.value('appcompress/sample', False, bool)))
    samples.setToolTip('Reads small portions of up to 8 files and compresses temporary copies using the chosen '
                       'algorithm. Originals are not changed. Sample files are deleted afterward.')
    samples.toggled.connect(lambda on: sec.qs.setValue('appcompress/sample', on))

    def preferences():
        try:
            value = json.loads(str(sec.qs.value('appcompress/folder_options', '{}', str)))
            return {k: v for k, v in value.items() if isinstance(v, dict)} if isinstance(value, dict) else {}
        except (ValueError, TypeError):
            return {}

    def options(path):
        own = preferences().get(os.path.normcase(os.path.abspath(path)), {})
        return {'algorithm': own.get('algorithm', algorithm.currentData()),
                'skip_extensions': sorted(appcompress.exclusions(own.get('skip_extensions', skip.values())))}

    def snapshot():
        return {'algorithm_name': algorithm.currentData(), 'skip_extensions': skip.values(), 'per_folder': preferences(),
                'sample': samples.isChecked()}

    sec.app_options, sec.app_snapshot = options, snapshot

    def refresh_options():
        if sec.result is None:
            return
        sec._remember_checks()
        for item in sec.result.items:
            selected = options(item.path)
            if any(item.extra.get(key) != value for key, value in selected.items()):
                item.extra.update(selected, estimate_pending=True)
                item.size = 0
        sec._fill(sec.result)

    algorithm.currentIndexChanged.connect(refresh_options)
    skip.changed.connect(refresh_options)
    own_button = button('Folder Settings…', '', None, 'Choose algorithm and exclusions for one checked folder')

    def edit():
        chosen = [c for _, c in sec._items(True)]
        if len(chosen) != 1:
            inform(sec, 'Check exactly one folder to change its settings.')
            return
        candidate = chosen[0]
        current = options(candidate.path)
        dialog = QDialog(sec)
        dialog.setWindowTitle(f'Settings for {candidate.title}')
        layout = dialog_layout(dialog, f'Settings for {candidate.title}',
                               'Saved for this folder, and used again when it\'s recompressed after updates.')
        own_group, combo = algorithm_picker(current['algorithm'])
        combo.setAccessibleName('Folder compression algorithm')
        fields = FileTypePicker(current['skip_extensions'])
        fields.setAccessibleName('Folder file types to skip')
        layout.addWidget(own_group)
        layout.addWidget(fields)
        layout.addLayout(dialog_buttons(dialog, ('Save Settings', dialog.accept)))
        if dialog.exec() == QDialog.Accepted:
            stored = preferences()
            stored[os.path.normcase(os.path.abspath(candidate.path))] = {
                'algorithm': combo.currentData(), 'skip_extensions': fields.values()}
            sec.qs.setValue('appcompress/folder_options', json.dumps(stored))
            refresh_options()

    own_button.clicked.connect(edit)
    community = button('Community Results…', '', None, 'Search optional public game compression estimates')

    def show_results():
        from .game_results import GameResultsDialog

        if not hasattr(sec, '_game_results'):
            sec._game_results = GameResultsDialog(sec)
        sec._game_results.show()
        sec._game_results.raise_()

    community.clicked.connect(show_results)
    return [('Algorithm', group), ('Skip file types', skip), ('', own_button), ('', community), ('', samples),
            ('', watch), ('', idle), ('', tray)]


def _takeout_options(sec: ToolSection):
    albums = QComboBox()
    for label, value in (("As links, using no extra space", "link"), ("As copies", "copy"), ("Leave out", "skip")):
        albums.addItem(label, value)
    albums.setToolTip("Takeout puts a copy of every album photo in the year folders as well. Links keep your albums "
                      "as folders without storing each photo twice.")
    albums.setCurrentIndex(max(0, albums.findData(sec.qs.value("cleanup/takeout/albums", "link", str))))
    albums.currentIndexChanged.connect(lambda: sec.qs.setValue("cleanup/takeout/albums", albums.currentData()))
    edited = QComboBox()
    for label, value in (("Keep both", "both"), ("Only the edited one", "edited"), ("Only the original", "original")):
        edited.addItem(label, value)
    edited.setToolTip("Photos you edited in Google Photos come out twice, as the original and an \"-edited\" copy.")
    edited.setCurrentIndex(max(0, edited.findData(sec.qs.value("cleanup/takeout/edited", "both", str))))
    edited.currentIndexChanged.connect(lambda: sec.qs.setValue("cleanup/takeout/edited", edited.currentData()))
    sec.takeout_albums, sec.takeout_edited = albums, edited
    return [("Albums", albums), ("Edited photos", edited)]


def _scan_caches(sec: ToolSection, progress, cancel):
    return caches.find_caches(progress, cancel)


def _protected(sec: ToolSection) -> list[str]:
    return [str(x) for x in (sec.qs.value("cleanup/devclean/protected", [], list) or []) if x]


def _set_protected(sec: ToolSection, ids: list[str]) -> None:
    sec.qs.setValue("cleanup/devclean/protected", sorted(set(ids)))
    _refresh_protected(sec)


def _protected_name(pid: str) -> str:
    kind, _, value = pid.partition(":")
    if kind == "remote":
        return value.rstrip("/").rsplit("/", 1)[-1].rsplit(":", 1)[-1] + "  (git remote)"
    return Path(value).name or value


def _refresh_protected(sec: ToolSection) -> None:
    btn = getattr(sec, "protected_btn", None)
    if btn is not None:
        n = len(_protected(sec))
        btn.setText(f"Protected ({n})" if n else "Protected")


def _dev_off(sec: ToolSection, what: str) -> list[str]:
    """Switched-off kinds ("kinds") or folder names ("names")."""
    return [str(x) for x in (sec.qs.value(f"cleanup/devclean/off_{what}", [], list) or []) if x]


def _set_dev_off(sec: ToolSection, what: str, values: list[str]) -> None:
    sec.qs.setValue(f"cleanup/devclean/off_{what}", sorted(set(values)))
    _refresh_kinds(sec)


def _refresh_kinds(sec: ToolSection) -> None:
    btn = getattr(sec, "kinds_btn", None)
    if btn is not None:
        off = len(_dev_off(sec, "kinds")) + len(_dev_off(sec, "names"))
        btn.setText(f"Show ({off} off)" if off else "Show")


def _kinds_button(sec: ToolSection):
    """Switch whole kinds of folder on or off (every node_modules, every virtual environment...), plus the
    single folder names switched off from the context menu."""
    from PySide6.QtWidgets import QMenu

    btn = button("Show", "", "eye", "Choose which kinds of folders and caches to look for")
    menu = QMenu(btn)

    def toggle(kind: str, on: bool) -> None:
        off = [k for k in _dev_off(sec, "kinds") if k != kind]
        _set_dev_off(sec, "kinds", off if on else off + [kind])

    def fill():
        menu.clear()
        off = set(_dev_off(sec, "kinds"))
        for kind, label in devclean.KINDS.items():
            a = menu.addAction(label)
            a.setCheckable(True)
            a.setChecked(kind not in off)
            a.toggled.connect(lambda on, k=kind: toggle(k, on))
        names = _dev_off(sec, "names")
        if names:
            menu.addSeparator()
            for name in names:
                menu.addAction(f"Suggest {name} Folders Again",
                               lambda n=name: _set_dev_off(sec, "names", [x for x in _dev_off(sec, "names")
                                                                          if x != n]))
        menu.addSeparator()
        a = menu.addAction("Changes apply to the next scan.")
        a.setEnabled(False)

    menu.aboutToShow.connect(fill)
    btn.setMenu(menu)
    sec.kinds_btn = btn
    _refresh_kinds(sec)
    return btn


def _dev_options(sec: ToolSection):
    from PySide6.QtWidgets import QMenu

    opts = _days_option("devclean", 30, "Pre-select projects untouched for",
                        "Projects with changed files or git activity more recently are listed but not "
                        "pre-selected")(sec)
    btn = button("Protected", "", "lock", "These projects and folders are never listed. Right-click a result to add "
                                          "it here.")
    menu = QMenu(btn)

    def fill():
        menu.clear()
        ids = _protected(sec)
        if not ids:
            a = menu.addAction("Nothing is protected yet. Right-click a result to protect its project.")
            a.setEnabled(False)
            return
        for pid in ids:
            menu.addAction(f"Stop Protecting {_protected_name(pid)}",
                           lambda p=pid: _set_protected(sec, [x for x in _protected(sec) if x != p]))

    menu.aboutToShow.connect(fill)
    btn.setMenu(menu)
    sec.protected_btn = btn
    _refresh_protected(sec)
    return opts + [("", btn), ("", _kinds_button(sec))]


def _dev_menu(sec: ToolSection, items: list[Candidate]):
    """Right-click: never clean this project (remembered by its git remote, so moving it keeps it
    protected) or this one folder."""
    projects = {c.extra["project_id"]: Path(c.extra["project"]).name for c in items if c.extra.get("project_id")}
    entries = []

    def protect(ids: list[str]) -> None:
        _set_protected(sec, _protected(sec) + ids)
        if sec.result is not None:
            prot = set(_protected(sec))
            sec._remember_checks()
            sec.result.items = [c for c in sec.result.items if c.extra.get("project_id") not in prot
                                and "path:" + os.path.normcase(str(c.path)) not in prot]
            sec._fill(sec.result)

    if len(projects) == 1:
        (pid, name), = projects.items()
        entries.append((f"Never Clean {name}", lambda: protect([pid])))
    elif projects:
        entries.append((f"Never Clean These {len(projects)} Projects", lambda: protect(list(projects))))
    names = sorted({c.path.name for c in items if not c.extra.get("docker") and c.extra.get("project")})
    if len(names) == 1:
        def hide_name(name=names[0]) -> None:
            _set_dev_off(sec, "names", _dev_off(sec, "names") + [name])
            if sec.result is not None:
                sec._remember_checks()
                sec.result.items = [c for c in sec.result.items if c.path.name != name or not c.extra.get("project")]
                sec._fill(sec.result)

        entries.append((f"Never Suggest {names[0]} Folders", hide_name))
    if len(items) == 1 and not items[0].extra.get("docker"):
        c = items[0]
        entries.append(("Never Clean This Folder", lambda: protect(["path:" + os.path.normcase(str(c.path))])))
    return entries


def _scan_dev(sec: ToolSection, progress, cancel):
    return devclean.find_dev_clutter(sec.folders, sec.extra_days.value(), protected=set(_protected(sec)),
                                     kinds_off=set(_dev_off(sec, "kinds")), names_off=set(_dev_off(sec, "names")),
                                     progress=progress, cancel=cancel)


def _dup_group_title(g: str, items: list[Candidate]) -> str:
    return f"{len(items) + 1} copies of {items[0].keeper.name}"


def tools() -> list[Tool]:
    return [
        Tool("duplicates", "Duplicates",
             "Finds files with exactly the same contents, whatever their names. Keep one copy and move the others "
             f"to the {BIN_NAME}, or replace them with hard links so every copy keeps working but the space is only "
             "used once.",
             lambda: _existing("Pictures", "Videos", "Documents", "Downloads", "Desktop", "Music"),
             _scan_duplicates,
             [(actions.HARDLINK, "Replace with Hard Links", ""), (actions.TRASH, f"Move to {BIN_NAME}", "primary")],
             options=_keep_options, group_title=_dup_group_title),
        Tool("similar", "Similar Photos",
             "Finds photos that look alike even when the files are different, like re-saved, resized or edited "
             "copies, and burst shots on Similar. The best photo in each group stays. Only obvious copies are "
             "selected for you, and you can hover a row to see the photo.",
             lambda: _existing("Pictures", "Desktop", "Downloads"), _scan_similar,
             [(actions.TRASH, f"Move to {BIN_NAME}", "primary")],
             options=_similar_options, group_title=_similar_group_title),
        Tool("videos", "Similar Videos",
             "Finds the same video saved more than once, like a smaller copy, another format or a second download. "
             "The copy with the most pixels stays, and lower-resolution copies that look the same are selected for "
             "you.",
             lambda: _existing("Videos", "Pictures", "Downloads", "Desktop"),
             lambda s, p, c: videos.find_similar_videos(s.folders, strictness=s.extra_how.currentData(),
                                                        progress=p, cancel=c),
             [(actions.TRASH, f"Move to {BIN_NAME}", "primary")], options=_videos_options,
             group_title=videos.group_title),
        Tool("music", "Same Music",
             "Finds songs saved more than once, even as different files, like an MP3 and a FLAC of the same "
             "recording. The best-quality copy stays. Only copies in the same format as that one are selected for "
             "you.",
             lambda: _existing("Music", "Downloads", "Desktop", "Documents"), _scan_music,
             [(actions.TRASH, f"Move to {BIN_NAME}", "primary")], options=_music_options,
             group_title=music.group_title),
        Tool("large", "Large Files",
             "The biggest files in these folders, like old videos, disk images and forgotten downloads. Nothing is "
             "selected for you.",
             lambda: [Path.home()], _scan_large, [(actions.TRASH, f"Move to {BIN_NAME}", "primary")],
             options=_large_options),
        Tool("folders", "Folder Sizes",
             "How much space each folder takes, biggest first. Open a folder to see what's inside. Folders that "
             "apps and Windows need can't be selected.",
             lambda: [Path.home()], lambda s, p, c: folders.find_folder_sizes(s.folders, p, c),
             [(actions.TRASH, f"Move to {BIN_NAME}", "primary")]),
        Tool("extensions", "Wrong Extensions",
             "Files whose contents are another type than their name says, like a PNG called .jpg or an iPhone "
             "photo (HEIC) called .jpg. Some apps refuse to open them. Renaming fixes that, and can be undone.",
             lambda: [Path.home()], lambda s, p, c: extensions.find_wrong_extensions(s.folders, p, c),
             [(actions.RENAME, "Fix Names", "primary")]),
        Tool("broken", "Broken Files",
             "Photos, videos, music, archives and PDFs that can't be opened, or were cut short by an interrupted "
             "download or copy. Files that won't open at all are selected for you. Damaged ones often still partly "
             "open, so those are up to you.",
             lambda: _existing("Pictures", "Videos", "Music", "Documents", "Downloads", "Desktop"),
             lambda s, p, c: broken.find_broken(s.folders, s.extra_thorough.isChecked(), p, c),
             [(actions.TRASH, f"Move to {BIN_NAME}", "primary")], options=_broken_options),
        Tool("archives", "Archives",
             "Finds .zip, .7z and .tar archives that have already been extracted next to themselves. An archive is "
             "only suggested when every file in it is present and identical in the extracted folder.",
             lambda: _existing("Downloads", "Desktop", "Documents"), lambda s, p, c: archives.find_extracted_archives(
                 s.folders, progress=p, cancel=c),
             [(actions.TRASH, f"Move to {BIN_NAME}", "primary")]),
        Tool("installers", "Installers",
             "Installers and disk images you downloaded a while ago. Installers for programs you seem to have "
             "installed are marked.",
             lambda: _existing("Downloads"),
             lambda s, p, c: installers.find_installers(s.folders, s.extra_days.value(), p, c),
             [(actions.TRASH, f"Move to {BIN_NAME}", "primary")],
             options=_days_option("installers", 30, "Older than", "Only list files older than this")),
        Tool("proxies", "Camera Proxies",
             "Low-resolution copies and thumbnails that GoPro, DJI and Insta360 cameras save next to every video "
             "for their phone apps. A file is only listed when its full video is right beside it.",
             lambda: _existing("Videos", "Pictures", "Desktop", "Downloads"),
             lambda s, p, c: proxies.find_proxies(s.folders, p, c),
             [(actions.TRASH, f"Move to {BIN_NAME}", "primary")]),
        Tool("pairs", "RAW + JPEG",
             "Photos the camera saved twice, as RAW and as JPEG or HEIC. One of each pair is listed; which one is "
             "up to you, and nothing is selected for you.",
             lambda: _existing("Pictures", "Desktop", "Downloads"),
             lambda s, p, c: pairs.find_pairs(s.folders, s.extra_keep.currentData(), p, c),
             [(actions.TRASH, f"Move to {BIN_NAME}", "primary")], options=_pairs_options),
        Tool("backups", "Phone Backups",
             "iPhone and iPad backups made by iTunes or Apple Devices, and Samsung Smart Switch backups, newest "
             "first. Each can be tens of gigabytes; nothing is selected for you.",
             None, lambda s, p, c: backups.find_backups(p, c),
             [(actions.TRASH, f"Move to {BIN_NAME}", "primary")],
             available=lambda: "" if os.name == "nt" else "Available on Windows."),
        Tool("apps", "Apps",
             "Installed apps, with the ones you haven't opened in months first. Uninstalling runs the app's own "
             f"uninstaller, then moves the folders it left behind to the {BIN_NAME}.",
             None, lambda s, p, c: apps_mod.find_apps(p, c),
             [(actions.UNINSTALL, "Uninstall…", "primary")],
             note="When an app was last opened comes from Windows' own records for your account. Apps started "
                  "another way, like by a game launcher, may show no record.",
             available=lambda: "" if apps_mod.IS_WINDOWS else "Available on Windows."),
        Tool("leftovers", "App Leftovers",
             "Folders in AppData and ProgramData that no installed app seems to use and that haven't changed in "
             "months, often left by apps you removed. Nothing is selected for you.",
             None, lambda s, p, c: apps_mod.find_leftovers(p, c),
             [(actions.TRASH, f"Move to {BIN_NAME}", "primary")],
             available=lambda: "" if apps_mod.IS_WINDOWS else "Available on Windows."),
        Tool("empty", "Empty Items",
             "Empty folders and empty files. Files that matter even when empty, like .gitkeep, are left alone.",
             lambda: _existing("Documents", "Downloads", "Desktop", "Pictures"),
             lambda s, p, c: empties.find_empty(s.folders, p, c),
             [(actions.TRASH, f"Move to {BIN_NAME}", "primary")]),
        Tool("caches", "Caches",
             "Caches that apps rebuild by themselves, from browsers, Discord, Spotify, Adobe apps, Telegram, Slack, "
             "Teams and games, plus leftover driver installers. Developer caches are on the Developer tab.",
             None, _scan_caches, [(actions.TRASH, f"Move to {BIN_NAME}", "primary")]),
        Tool("devclean", "Developer",
             "Folders in your code projects that tools can rebuild, like node_modules, build output and virtual "
             "environments, plus package caches like npm, Gradle and pip. Source code and anything in git is left "
             "alone.",
             lambda: [Path.home()], _scan_dev, [(actions.TRASH, f"Move to {BIN_NAME}", "primary")],
             folder_label="Projects in",
             options=_dev_options, menu=_dev_menu,
             note="A folder is only listed when the file that rebuilds it sits next to it, like package.json "
                  f"beside node_modules. Everything goes to the {BIN_NAME}, so you can restore it later.",
             group_title=devclean.group_title),
        Tool("winclean", "Windows",
             "Files Windows leaves behind, like the previous installation after a big update, update downloads, "
             "error reports, memory dumps and old temporary files.",
             None, lambda s, p, c: winclean.find(p, c), [(actions.TRASH, "Clean Up", "primary")],
             note="System items are removed with the same Windows tools Disk Cleanup uses. Your own files go to "
                  f"the {BIN_NAME}.",
             available=lambda: "" if winclean.supported() else "Available on Windows only."),
        Tool("appcompress", "Compress Apps",
             "Compresses games and apps on NTFS drives. They keep working as usual, and Windows decompresses files "
             "as they're read.",
             appcompress.default_sources,
             lambda s, p, c: appcompress.find_compressible(appcompress.expand_sources(s.folders), p, c, **s._app_scan_options),
             [(actions.COMPACT, "Compress Folders", "primary"), (actions.DECOMPACT, "Decompress Folders", "secondary")],
             options=_appcompress_options,
             available=lambda: "" if appcompress.supported() else "Available on Windows (NTFS drives) only."),
        Tool("takeout", "Google Takeout",
             "Turns a Google Photos Takeout into one folder of photos and videos, each with the date it was taken. "
             "Google stores those dates in separate files, so without this every photo shows the export date. If "
             "it's stopped, running it again picks up where it left off.",
             lambda: _existing("Downloads"), lambda s, p, c: takeout.find(s.folders, p, c),
             [(actions.TAKEOUT, "Merge Into Folder…", "primary")], folder_label="Takeout files in",
             options=_takeout_options),
    ]

