"""Data relocation retains source and switches only after a successful copy."""

import json
import sqlite3

import pytest

from quill_social import paths


@pytest.fixture
def default_root(tmp_path, monkeypatch):
    monkeypatch.delenv("QUILLSOCIAL_DATA", raising=False)
    monkeypatch.delenv("QUILL_APP_ROOT", raising=False)
    root = tmp_path / "default"
    monkeypatch.setattr(paths, "default_data_dir", lambda: root)
    return root


def test_migration_copies_wal_and_keeps_source(default_root, tmp_path):
    source = paths.data_dir()
    connection = sqlite3.connect(source / "social.db")
    connection.execute("PRAGMA journal_mode=WAL")
    connection.execute("CREATE TABLE accounts (credential_ref TEXT)")
    connection.execute("INSERT INTO accounts VALUES ('os-keyring:account')")
    connection.commit()
    (source / "a11y.json").write_text('{"ui_mode":"standard"}')
    destination = tmp_path / "relocated"
    try:
        paths.migrate_data_location(source, destination)
        assert paths.data_dir() == destination
        with sqlite3.connect(destination / "social.db") as copied:
            assert copied.execute("SELECT * FROM accounts").fetchall() == [("os-keyring:account",)]
        assert (source / "social.db").exists()
        assert (destination / "a11y.json").read_text() == (source / "a11y.json").read_text()
    finally:
        connection.close()


def test_failed_migration_keeps_pointer(default_root, tmp_path, monkeypatch):
    source = paths.data_dir()
    (source / "a11y.json").write_text("{}")
    def fail(*args, **kwargs):
        raise OSError("Disk full")
    monkeypatch.setattr(paths.shutil, "copy2", fail)
    with pytest.raises(OSError, match="Disk full"):
        paths.migrate_data_location(source, tmp_path / "destination")
    assert paths.data_dir() == source
    assert (source / "a11y.json").read_text() == "{}"


def test_rejects_existing_data_and_nested_locations(default_root, tmp_path):
    source = paths.data_dir()
    for target in (source / "nested", source.parent):
        with pytest.raises(ValueError):
            paths.validate_data_location(source, target)
    destination = tmp_path / "used"
    destination.mkdir()
    (destination / "important.txt").write_text("keep")
    with pytest.raises(ValueError, match="empty"):
        paths.migrate_data_location(source, destination)
    assert (destination / "important.txt").read_text() == "keep"


def test_unavailable_pointer_does_not_silently_create_new_store(default_root):
    default_root.mkdir()
    (default_root / "data-location.json").write_text(json.dumps({"data_folder": str(default_root / "gone")}))
    with pytest.raises(OSError, match="unavailable"):
        paths.data_dir()


def test_environment_override_wins_and_prevents_relocation(default_root, tmp_path, monkeypatch):
    monkeypatch.setenv("QUILLSOCIAL_DATA", str(tmp_path / "override"))
    source = paths.data_dir()
    assert source == tmp_path / "override" / paths.APP_DIRNAME
    with pytest.raises(ValueError, match="environment"):
        paths.migrate_data_location(source, tmp_path / "elsewhere")
