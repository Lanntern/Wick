"""What's in "Everything else" (wick/systemspace.py)."""

from pathlib import Path

from wick import systemspace


def test_hidden_system_files_are_read_from_the_folder_listing(tmp_path):
    (tmp_path / "hiberfil.sys").write_bytes(b"x" * 1000)
    (tmp_path / "pagefile.sys").write_bytes(b"x" * 500)
    (tmp_path / "other.txt").write_bytes(b"x")
    assert systemspace._root_files(tmp_path) == {"hiberfil.sys": 1000, "pagefile.sys": 500}


def test_linux_space_comes_with_what_to_run(monkeypatch):
    gb = 1024 ** 3
    sizes = {"/var/log/journal": 3 * gb, "/var/cache/apt/archives": gb, "/var/cache/dnf": 0,
             "/var/cache/pacman/pkg": 0}
    monkeypatch.setattr(systemspace, "IS_WINDOWS", False)
    monkeypatch.setattr(systemspace, "_size", lambda p: sizes.get(str(p), 0))
    monkeypatch.setattr(systemspace, "_snap_old_versions", lambda: 10)          # (too small to list)
    monkeypatch.setattr(systemspace.shutil, "which", lambda name: None)
    monkeypatch.setattr(systemspace, "SWAP_FILES", ())                          # (the test machine's own)
    items = systemspace.measure(Path("/"))
    assert [h.key for h in items][:2] == ["journal", "packages_apt"]
    assert items[0].command.startswith("sudo journalctl") and items[1].command == "sudo apt clean"
    assert all(h.size >= systemspace.MIN_SIZE for h in items)
