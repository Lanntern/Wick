"""Installed apps, uninstalling with leftovers, and leftovers of removed apps (wick/cleanup/apps.py)."""

import os
import struct
import time
from pathlib import Path

from wick.cleanup import apps


def test_uninstall_commands_are_split_the_way_windows_runs_them():
    assert apps.split_command('"C:\\Program Files\\Foo\\unins000.exe" /SILENT') == (
        "C:\\Program Files\\Foo\\unins000.exe", "/SILENT")
    assert apps.split_command("C:\\Program Files\\Bar\\uninstall.exe --remove") == (
        "C:\\Program Files\\Bar\\uninstall.exe", "--remove")
    msi = apps.App("Baz", uninstall="MsiExec.exe /I{1234-ABCD}")
    assert apps.uninstall_command(msi) == ("MsiExec.exe", "/X{1234-ABCD}")


def test_last_opened_comes_from_userassist_records():
    filetime = int((time.time() - 3 * 86400 + 11_644_473_600) * 10_000_000)
    raw = bytes(60) + struct.pack("<Q", filetime) + bytes(4)
    assert abs(apps._filetime(raw) - (time.time() - 3 * 86400)) < 5
    assert apps._filetime(bytes(20)) is None

    app = apps.App("Paint.NET 5.0 (x64)", location=Path("C:/Program Files/paint.net"))
    other = apps.App("Never Used", location=Path("C:/Program Files/never"))
    runs = {"c:\\program files\\paint.net\\paintdotnet.exe".replace("\\", os.sep): 1000.0,
            "c:/users/me/start menu/programs/paint.net.lnk": 2000.0}
    apps.attach_last_used([app, other], runs)
    assert app.last_used == 2000.0 and other.last_used is None


def _appdata(tmp_path, monkeypatch):
    roaming, local, programdata = tmp_path / "Roaming", tmp_path / "Local", tmp_path / "ProgramData"
    for d in (roaming, local, programdata):
        d.mkdir()
    monkeypatch.setenv("APPDATA", str(roaming))
    monkeypatch.setenv("LOCALAPPDATA", str(local))
    monkeypatch.setenv("PROGRAMDATA", str(programdata))
    return roaming, local, programdata


def test_an_uninstalled_apps_folders_are_found_but_shared_names_are_not(tmp_path, monkeypatch):
    roaming, local, programdata = _appdata(tmp_path, monkeypatch)
    (roaming / "CoolEditor").mkdir()
    (local / "Cool Editor").mkdir()
    (programdata / "Acme").mkdir()
    (programdata / "Acme" / "Cool Editor").mkdir()
    (roaming / "Microsoft").mkdir()
    install = tmp_path / "Programs" / "Cool Editor"
    install.mkdir(parents=True)
    app = apps.App("Cool Editor 2.1 (x64)", publisher="Acme", location=install)
    found = set(apps.leftovers_of(app, others=[apps.App("Something Else", publisher="Other Co")]))
    assert found == {install, roaming / "CoolEditor", local / "Cool Editor", programdata / "Acme"}
    # Another installed app has the same name: its folders aren't this one's leftovers.
    assert set(apps.leftovers_of(app, others=[apps.App("Cool Editor")])) == {install}


def test_leftovers_of_removed_apps_are_only_quiet_unclaimed_folders(tmp_path, monkeypatch):
    roaming, local, _ = _appdata(tmp_path, monkeypatch)
    monkeypatch.setattr(apps, "IS_WINDOWS", True)
    monkeypatch.setattr(apps, "_running_folders", lambda: set())
    old = time.time() - 200 * 86400

    def folder(path: Path, size: int, mtime: float) -> None:
        path.mkdir()
        f = path / "data.bin"
        f.write_bytes(b"x" * size)
        os.utime(f, (mtime, mtime))
        os.utime(path, (mtime, mtime))

    folder(roaming / "GoneApp", 2_000_000, old)          # nobody's: listed
    folder(roaming / "Spotify", 2_000_000, old)          # an installed app's: not listed
    folder(local / "RecentThing", 2_000_000, time.time())  # changed lately: not listed
    folder(local / "TinyOld", 100, old)                  # too small to matter
    folder(local / "Microsoft", 2_000_000, old)          # Windows' own
    result = apps.find_leftovers(apps=[apps.App("Spotify", publisher="Spotify AB")])
    assert [c.title for c in result.items] == ["GoneApp"]
    c = result.items[0]
    assert not c.selected and c.warning and "months" in c.detail


def test_uninstalling_moves_what_the_app_left_to_the_bin(tmp_path, monkeypatch):
    from wick import journal
    from wick.cleanup import actions
    from wick.cleanup.common import Candidate

    roaming, _, _ = _appdata(tmp_path, monkeypatch)
    (roaming / "Cool Editor").mkdir()
    (roaming / "Cool Editor" / "settings.json").write_text("{}")
    app = apps.App("Cool Editor", publisher="Acme", uninstall='"C:/x/unins000.exe"')
    monkeypatch.setattr(apps, "installed", lambda: [app, apps.App("Other")])
    ran = []
    monkeypatch.setattr(apps, "uninstall", lambda a, cancel=None: ran.append(a.name) or (True, "Uninstalled"))
    item = Candidate(Path(app.name), 5_000_000, title=app.name, extra={"app": app})
    outcomes, run_id = actions.apply([item], actions.UNINSTALL, "apps", tmp_path)
    assert ran == ["Cool Editor"] and outcomes[0].ok and "1 leftover folder" in outcomes[0].message
    assert not (roaming / "Cool Editor").exists()
    run = journal.load_run(next(journal.runs_dir().glob(f"*{run_id}*")))
    modes = [f.mode for f in run.files]
    assert "uninstall" in modes and "removed" in modes          # (the leftovers can be restored from History)

    # The uninstaller was cancelled: nothing else happens.
    monkeypatch.setattr(apps, "uninstall", lambda a, cancel=None: (False, "The uninstaller closed without removing it"))
    (roaming / "Cool Editor").mkdir()
    outcomes, _ = actions.apply([item], actions.UNINSTALL, "apps", tmp_path)
    assert not outcomes[0].ok and (roaming / "Cool Editor").exists()
