"""Camera proxy files, and Windows Installer packages no installed program lists."""

import time
from pathlib import Path

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
