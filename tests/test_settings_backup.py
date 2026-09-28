import json

import pytest

from quill_social.services import settings_backup as backups


def test_round_trip_preserves_preferences_shortcuts_and_excludes_database(tmp_path):
    preferences = tmp_path / "a11y.json"
    shortcuts = tmp_path / "keymap.json"
    globals_path = tmp_path / "global-shortcuts.json"
    preferences.write_text('{"ui_mode":"advanced","display_timezone":"utc"}')
    shortcuts.write_text('{"compose":"Alt+N"}')
    globals_path.write_text('{"show_window":"Ctrl+Alt+S"}')
    database = tmp_path / "social.db"
    database.write_bytes(b"private accounts and drafts")
    saved = backups.create_backup(tmp_path)
    assert set(backups.read_backup(saved)["files"]) == set(backups.FILES)
    preferences.write_text('{"ui_mode":"standard"}')
    shortcuts.write_text('{}')
    globals_path.write_text('{}')
    recovery = backups.restore_backup(saved, tmp_path)
    assert json.loads(preferences.read_text())["ui_mode"] == "advanced"
    assert json.loads(shortcuts.read_text())["compose"] == "Alt+N"
    assert json.loads(globals_path.read_text()) == {"show_window": "Ctrl+Alt+S"}
    assert backups.read_backup(recovery)["files"]["a11y.json"] == {"ui_mode": "standard"}
    assert database.read_bytes() == b"private accounts and drafts"


def test_defaults_and_unchanged_backup(tmp_path):
    saved = backups.create_backup(tmp_path)
    assert backups.create_backup(tmp_path, only_if_changed=True) == saved
    (tmp_path / "a11y.json").write_text('{"ui_mode":"advanced"}')
    backups.restore_backup(saved, tmp_path)
    assert not (tmp_path / "a11y.json").exists()


@pytest.mark.parametrize("mutation", [
    lambda data: data.update(format="leasey"),
    lambda data: data.update(version=2),
    lambda data: data["files"].update({"../accounts.json": {}}),
    lambda data: data["files"].update({"a11y.json": {"ui_mode": "broken"}}),
    lambda data: data["files"].update({"a11y.json": {"high_contrast": "false"}}),
    lambda data: data["files"].update({"keymap.json": {"compose": 5}}),
    lambda data: data["files"].update({"global-shortcuts.json": {"compose": 5}}),
    lambda data: data["files"].update({"global-shortcuts.json": {"unknown": "Ctrl+Alt+U"}}),
    lambda data: data["files"].update({"global-shortcuts.json": {"compose": "N"}}),
    lambda data: data["files"].update({"global-shortcuts.json": {"compose": "Ctrl+Invalid"}}),
    lambda data: data["files"].update({"global-shortcuts.json": {
        "compose": "Ctrl+Alt+N", "show_window": "alt+ctrl+n"}}),
])
def test_invalid_backup_rejected_before_settings_change(tmp_path, mutation):
    settings = tmp_path / "a11y.json"
    settings.write_text('{"ui_mode":"standard"}')
    saved = backups.create_backup(tmp_path)
    data = backups.read_backup(saved)
    mutation(data)
    saved.write_text(json.dumps(data))
    with pytest.raises(ValueError):
        backups.restore_backup(saved, tmp_path)
    assert settings.read_text() == '{"ui_mode":"standard"}'
    assert len(backups.list_backups(tmp_path)) == 1


def test_second_write_failure_rolls_back_exact_original_bytes(tmp_path, monkeypatch):
    prefs = tmp_path / "a11y.json"
    keys = tmp_path / "keymap.json"
    prefs.write_text('{"ui_mode":"advanced"}')
    keys.write_text('{"compose":"Alt+N"}')
    saved = backups.create_backup(tmp_path)
    original = b'{ "ui_mode" : "standard" }\n'
    prefs.write_bytes(original)
    keys.unlink()
    real_write = backups._atomic_write

    def fail_keys(path, content):
        if path == keys:
            raise OSError("disk write failed")
        return real_write(path, content)

    monkeypatch.setattr(backups, "_atomic_write", fail_keys)
    with pytest.raises(OSError, match="disk write failed"):
        backups.restore_backup(saved, tmp_path)
    assert prefs.read_bytes() == original
    assert not keys.exists()
    assert len(backups.list_backups(tmp_path)) == 2


def test_older_backup_preserves_current_global_shortcuts(tmp_path):
    saved = backups.create_backup(tmp_path)
    data = backups.read_backup(saved)
    del data["files"]["global-shortcuts.json"]
    saved.write_text(json.dumps(data))
    globals_path = tmp_path / "global-shortcuts.json"
    original = b'{ "show_window": "Ctrl+Alt+S" }\n'
    globals_path.write_bytes(original)
    backups.restore_backup(saved, tmp_path)
    assert globals_path.read_bytes() == original


def test_global_defaults_are_restored_from_new_backup(tmp_path):
    saved = backups.create_backup(tmp_path)
    globals_path = tmp_path / "global-shortcuts.json"
    globals_path.write_text('{"show_window":"Ctrl+Alt+S"}')
    backups.restore_backup(saved, tmp_path)
    assert not globals_path.exists()


def test_global_write_failure_rolls_back_preferences_and_keymap(tmp_path, monkeypatch):
    prefs = tmp_path / "a11y.json"
    keys = tmp_path / "keymap.json"
    globals_path = tmp_path / "global-shortcuts.json"
    prefs.write_text('{"ui_mode":"advanced"}')
    keys.write_text('{"compose":"Alt+N"}')
    globals_path.write_text('{"show_window":"Ctrl+Alt+S"}')
    saved = backups.create_backup(tmp_path)
    originals = {prefs: b'{ "ui_mode": "standard" }\n', keys: b'{}', globals_path: b'{}'}
    for path, content in originals.items():
        path.write_bytes(content)
    real_write = backups._atomic_write

    def fail_globals(path, content):
        if path == globals_path:
            raise OSError("global write failed")
        return real_write(path, content)

    monkeypatch.setattr(backups, "_atomic_write", fail_globals)
    with pytest.raises(OSError, match="global write failed"):
        backups.restore_backup(saved, tmp_path)
    for path, content in originals.items():
        assert path.read_bytes() == content
