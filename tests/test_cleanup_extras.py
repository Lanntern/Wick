"""Camera proxy files, and Windows Installer packages no installed program lists."""

import os
import sys
import time
from pathlib import Path

import pytest

from wick.cleanup import proxies, winclean


def test_camera_proxies_are_listed_only_beside_their_video(tmp_path):
    trip = tmp_path / "trip"
    trip.mkdir()
    for name in ("GX010123.MP4", "GX010123.LRV", "GX010123.THM", "GX010124.LRV",          # (124: no video)
                 "DJI_0001.MP4", "DJI_0001.LRF",
                 "VID_20240101_120000_00_007.insv", "LRV_20240101_120000_11_007.insv",
                 "LRV_20240101_130000_11_008.insv", "notes.txt"):
        (trip / name).write_bytes(b"x" * 1000)
    found = {c.path.name for c in proxies.find_proxies([tmp_path]).items}
    assert found == {"GX010123.LRV", "GX010123.THM", "DJI_0001.LRF", "LRV_20240101_120000_11_007.insv"}
    names = {"gx010123.mp4"}
    assert proxies.full_video(Path("GX010123.lrv"), names) == "GX010123.mp4"
    assert proxies.full_video(Path("GX010123.MP4"), names) == ""


def test_installer_orphans_are_only_unlisted_old_packages():
    now = time.time()
    old, new = now - 90 * 86400, now - 86400
    folder = Path("C:/Windows/Installer")
    files = [(folder / "1a.msi", 100, old), (folder / "2b.msp", 200, old), (folder / "3c.msi", 300, new),
             (folder / "4d.msi", 400, old), (folder / "readme.txt", 5, old)]
    import os

    registered = {os.path.normcase(str(folder / "4d.msi"))}
    got = winclean.orphans_of(files, registered, now)
    assert [p.name for p, _n in got] == ["1a.msi", "2b.msp"]          # (3c is recent, 4d is listed)
    assert winclean.orphans_of(files, set(), now) == []               # Windows' list unreadable: nothing


def test_the_removal_script_rechecks_every_path():
    script = winclean.script(["installer_orphans"], [r"C:\Windows\Installer\o'k.msp"])
    assert "'C:\\Windows\\Installer\\o''k.msp'" in script
    assert "Split-Path -Parent $f) -eq $installer" in script and "\\.ms[ip]$" in script
    assert "installer" not in winclean.script(["memory_dumps"], [r"C:\x.msi"]).lower()


def test_raw_and_jpeg_pairs(tmp_path):
    from wick.cleanup import pairs

    shoot = tmp_path / "shoot"
    shoot.mkdir()
    for name in ("DSC0001.ARW", "DSC0001.JPG", "DSC0002.ARW", "IMG_5.CR3", "IMG_5.HEIC", "IMG_6.JPG",
                 "two.NEF", "two.jpg", "two.heic"):                       # (two developed copies: left alone)
        (shoot / name).write_bytes(b"x" * 100)
    jpegs = pairs.find_pairs([tmp_path], keep="raw").items
    assert sorted(c.path.name for c in jpegs) == ["DSC0001.JPG", "IMG_5.HEIC"]
    assert not any(c.selected for c in jpegs) and jpegs[0].keeper.suffix.lower() in (".arw", ".cr3")
    raws = pairs.find_pairs([tmp_path], keep="jpeg").items
    assert sorted(c.path.name for c in raws) == ["DSC0001.ARW", "IMG_5.CR3"]


def test_phone_backups_are_listed_newest_first(tmp_path, monkeypatch):
    import datetime
    import plistlib

    from wick.cleanup import backups

    base = tmp_path / "Apple Computer" / "MobileSync" / "Backup"
    for udid, name, year in (("00008030-old", "Old iPhone", 2019), ("00008110-new", "Anna's iPhone", 2026)):
        folder = base / udid
        folder.mkdir(parents=True)
        (folder / "Manifest.db").write_bytes(b"x" * 5000)
        with open(folder / "Info.plist", "wb") as f:
            plistlib.dump({"Device Name": name, "Product Name": "iPhone 15", "Last Backup Date":
                           datetime.datetime(year, 5, 1)}, f)
    monkeypatch.setenv("APPDATA", str(tmp_path))
    monkeypatch.setenv("USERPROFILE", str(tmp_path / "nobody"))
    items = backups.find_backups().items
    assert [c.title for c in items] == ["Anna's iPhone (iPhone 15) backup", "Old iPhone (iPhone 15) backup"]
    assert all(not c.selected and c.warning and c.size >= 5000 for c in items)
    assert "2019" in items[1].detail and "months ago" in items[1].detail


VBOX_HDDS = """UUID:           1b2c3d4e-0000-4000-8000-000000000001
Parent UUID:    base
State:          created
Type:           normal (base)
Location:       {disk}
Storage format: VDI
Capacity:       51200 MBytes
Encryption:     disabled
In use by VMs:  Ubuntu (UUID: 9a9a9a9a-0000-4000-8000-0000000000aa)

UUID:           1b2c3d4e-0000-4000-8000-000000000002
Location:       {other}
Storage format: VMDK
In use by VMs:  Old (UUID: 9a9a9a9a-0000-4000-8000-0000000000bb)
"""


@pytest.mark.skipif(os.name == "nt", reason="the stand-in VBoxManage is a shell script")
def test_virtualbox_and_vmware_disks_are_found_and_compacted(tmp_path, monkeypatch):
    import os
    import stat

    from wick.cleanup import vdisk

    disk = tmp_path / "Ubuntu.vdi"
    disk.write_bytes(b"\0" * 4096)
    calls = tmp_path / "calls.txt"
    tool = tmp_path / "bin" / "VBoxManage"
    tool.parent.mkdir()
    listing = tmp_path / "hdds.txt"
    listing.write_text(VBOX_HDDS.format(disk=disk, other=tmp_path / "old.vmdk"))
    tool.write_text(f"#!/bin/sh\necho \"$@\" >> {calls}\n"
                    f"if [ \"$1 $2\" = \"list hdds\" ]; then cat {listing}; fi\n"
                    "if [ \"$1\" = modifymedium ]; then printf '%01024d' 0 > \"$3\"; fi\n")       # (1 KB left)
    tool.chmod(tool.stat().st_mode | stat.S_IEXEC)
    monkeypatch.setenv("PATH", f"{tool.parent}{os.pathsep}{os.environ['PATH']}")
    monkeypatch.setattr(vdisk, "MIN_FILE", 1000)
    home = tmp_path / "home"
    vm = home / "vmware" / "Win11"
    vm.mkdir(parents=True)
    (vm / "Win11.vmdk").write_bytes(b"\0" * 5000)
    (vm / "Win11-s001.vmdk").write_bytes(b"\0" * 5000)            # (a piece of the disk, not a disk)
    (vm / "Win11.vmx.lck").mkdir()                                # (the machine is on)
    monkeypatch.setattr(vdisk.Path, "home", staticmethod(lambda: home))

    parsed = vdisk.parse_vbox_hdds(VBOX_HDDS.format(disk=disk, other="x.vmdk") +
                                   "\nRUNNING:\n\"Ubuntu\" {9a9a9a9a-0000-4000-8000-0000000000aa}\n")
    assert parsed[0] == (disk, "VDI", True) and parsed[1][1] == "VMDK" and not parsed[1][2]
    items = {c.extra["vdisk"]: c for c in vdisk.vm_candidates()}
    assert set(items) == {"vbox", "vmware"} and not any(c.selected for c in items.values())
    assert items["vmware"].extra["busy"] and items["vmware"].warning
    results = {c.extra["vdisk"]: (ok, msg, freed) for c, ok, msg, freed in vdisk.compact(list(items.values()))}
    assert results["vbox"][0] and results["vbox"][2] == 4096 - 1024
    assert f"modifymedium disk {disk} --compact" in calls.read_text()
    assert not results["vmware"][0] and "shut it down" in results["vmware"][1]


def test_old_folders_are_suggested_for_windows_compression(tmp_path):
    import os
    import time

    from wick.cleanup import appcompress

    now = time.time()
    year_ago = now - 365 * 86400

    def folder(name, files, mtime):
        f = tmp_path / "Documents" / name
        f.mkdir(parents=True)
        for fname, size in files:
            (f / fname).write_bytes(b"\0" * size)
            os.utime(f / fname, (mtime, mtime))
        return f

    folder("old project", [("data.csv", 3000), ("notes.txt", 2000)], year_ago)
    folder("recent project", [("data.csv", 5000)], now)
    folder("old photos", [("a.jpg", 4000), ("b.mp4", 4000), ("x.txt", 100)], year_ago)
    folder("small", [("a.txt", 10)], year_ago)
    found = appcompress.old_folders([tmp_path / "Documents"], now=now, min_size=1000)
    assert [p.name for p in found] == ["old project"]


def test_mac_processes_are_read_from_ps_and_lsof():
    from wick.cleanup import inuse

    procs = {p.pid: p for p in inuse.parse_mac(
        "  101     1 /bin/zsh\n  202   101 /opt/homebrew/bin/node\n",
        "  101 -zsh\n  202 node /Users/me/site/node_modules/.bin/vite\n",
        "p101\nfcwd\nn/Users/me/site\np202\nfcwd\nn/Users/me/site\n")}
    assert procs[101].name == "zsh" and procs[101].cwd == "/Users/me/site"
    assert procs[202].ppid == 101 and "node_modules/.bin/vite" in procs[202].cmdline


@pytest.mark.skipif(sys.platform != "darwin", reason="APFS compression is macOS's")
def test_apfs_compression_keeps_files_the_same_and_undoes(tmp_path):
    from wick.cleanup import apfs

    app = tmp_path / "Game.app" / "Contents" / "Resources"
    app.mkdir(parents=True)
    data = b"level data " * 50_000
    f = app / "levels.dat"
    f.write_bytes(data)
    before = f.stat().st_blocks
    assert apfs.run(tmp_path / "Game.app", [f]) == 0
    assert f.read_bytes() == data and apfs.is_compressed(f.stat()) and f.stat().st_blocks < before
    assert apfs.run(tmp_path / "Game.app", [f], undo=True) == 0
    assert f.read_bytes() == data and not apfs.is_compressed(f.stat())


@pytest.mark.skipif(os.name == "nt", reason="macOS paths")
def test_apfs_refuses_system_and_whole_folders():
    from pathlib import Path as P

    from wick.cleanup import apfs

    for bad in (P("/"), P("/System/Library"), P("/Applications"), P("/usr/local/bin")):
        with pytest.raises(ValueError):
            apfs.check_folder(bad)
    apfs.check_folder(P("/Applications/Some.app"))


@pytest.mark.skipif(sys.platform != "darwin", reason="clonefile is APFS's")
def test_apfs_clones_are_separate_files_sharing_storage(tmp_path):
    from wick.fileops import clone_file, supports_block_clone

    src = tmp_path / "a.bin"
    src.write_bytes(b"x" * 300_000)
    if not supports_block_clone(src):
        pytest.skip("not on APFS")
    dst = tmp_path / "b.bin"
    clone_file(src, dst)
    assert dst.read_bytes() == src.read_bytes()
    dst.write_bytes(b"changed")                       # (a clone, not a link: the original stays as it was)
    assert src.read_bytes() == b"x" * 300_000


@pytest.mark.skipif(sys.platform != "darwin", reason="Quick Look is macOS's")
def test_quick_look_makes_thumbnails(tmp_path):
    from PIL import Image

    from wick.gui import macthumbs

    p = tmp_path / "photo.png"
    Image.new("RGB", (800, 600), (200, 30, 30)).save(p)
    png = macthumbs.thumbnail(p, 128)
    if png is None:
        pytest.skip("Quick Look made no thumbnail on this machine")
    assert png[:8] == b"\x89PNG\r\n\x1a\n"


def test_mac_app_leftovers_only_when_no_app_matches():
    from wick.cleanup import macclean

    ids = {"com.google.chrome", "com.spotify.client"}
    names = {"google chrome", "spotify", "safari"}
    assert macclean.is_leftover("com.oldvendor.gone", ids, names)
    assert macclean.is_leftover("OldApp", ids, names)
    for kept in ("com.google.Chrome", "com.spotify.client.helper", "Google", "Spotify", "com.apple.Safari",
                 "group.com.whatever", "CrashReporter"):
        assert not macclean.is_leftover(kept, ids, names), kept


@pytest.mark.skipif(os.name == "nt", reason="runs the workflow's shell line with bash")
def test_finder_quick_actions_are_written_and_removed(tmp_path, monkeypatch):
    import plistlib
    import shlex
    import subprocess

    from wick.gui import quickactions

    monkeypatch.setattr(quickactions.Path, "home", staticmethod(lambda: tmp_path))
    monkeypatch.setattr(quickactions, "refresh", lambda: None)
    record = tmp_path / "args.txt"
    stub = tmp_path / "wick stub.sh"                          # (a space in the path: quoting matters)
    stub.write_text(f'#!/bin/sh\nprintf "%s\\n" "$@" > {shlex.quote(str(record))}\n')
    stub.chmod(0o755)
    quickactions.install([str(stub)])
    assert quickactions.installed()
    convert = tmp_path / "Library" / "Services" / "Convert with Wick….workflow" / "Contents"
    doc = plistlib.loads((convert / "document.wflow").read_bytes())
    command = doc["actions"][0]["action"]["ActionParameters"]["COMMAND_STRING"]
    assert command.endswith('"$@" >/dev/null 2>&1 &') and "--convert choose" in command
    info = plistlib.loads((convert / "Info.plist").read_bytes())
    assert info["NSServices"][0]["NSMenuItem"]["default"] == "Convert with Wick…"
    # The shell line itself hands the files over (what Automator runs).
    target = tmp_path / "a photo.jpg"
    subprocess.run(["/bin/bash", "-c", command.replace(" &", ""), "bash", str(target)], check=True)
    assert record.read_text().split("\n")[:3] == ["--convert", "choose", str(target)]
    if sys.platform == "darwin" and Path("/usr/bin/automator").exists():
        record.unlink()
        flow = tmp_path / "Library" / "Services" / "Compress with Wick.workflow"
        subprocess.run(["/usr/bin/automator", "-i", str(target), str(flow)], capture_output=True, timeout=120)
        import time

        deadline = time.time() + 20
        while not record.exists() and time.time() < deadline:
            time.sleep(0.2)
        assert record.read_text().strip() == str(target)       # (Automator ran the workflow with the file)
    quickactions.uninstall()
    assert not quickactions.installed()
