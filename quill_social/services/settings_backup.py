"""Versioned backups of fork preferences and keyboard shortcuts only."""

from __future__ import annotations

import json
import os
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from quill_social.a11y import A11ySettings
from quill_social.keymap import validate_chord

FILES = ("a11y.json", "keymap.json", "global-shortcuts.json")
REQUIRED_FILES = {"a11y.json", "keymap.json"}
FORMAT = "quill-social-settings"
MAX_BYTES = 2 * 1024 * 1024


def backup_directory(data_dir: str | Path) -> Path:
    return Path(data_dir) / "settings-backups"


def _validate_files(files: object) -> dict:
    if (not isinstance(files, dict) or not REQUIRED_FILES.issubset(files)
            or not set(files).issubset(FILES)):
        raise ValueError("The backup must contain preferences and keyboard shortcuts only.")
    defaults = A11ySettings().to_dict()
    for name, values in files.items():
        if values is None:
            continue  # An absent settings file means use application defaults.
        if not isinstance(values, dict):
            raise ValueError(f"Invalid settings in {name}.")
        if name in {"keymap.json", "global-shortcuts.json"}:
            if any(not isinstance(key, str) or not isinstance(value, str)
                   for key, value in values.items()):
                raise ValueError("Keyboard shortcuts must map command names to text.")
            if name == "global-shortcuts.json":
                if not set(values).issubset({"show_window", "compose", "refresh"}):
                    raise ValueError("The backup contains an unsupported global shortcut command.")
                chords = [validate_chord(value, global_hotkey=True) for value in values.values()]
                assigned = [chord for chord in chords if chord]
                if len(assigned) != len(set(assigned)):
                    raise ValueError("Global shortcuts must use different keys.")
        else:
            for key, value in values.items():
                if key not in defaults or type(value) is not type(defaults[key]):
                    raise ValueError(f"Unsupported preference: {key}.")
            sanitized = A11ySettings.from_dict(values).to_dict()
            if any(sanitized[key] != value for key, value in values.items()):
                raise ValueError("The backup contains an invalid preference value.")
    return files


def read_backup(path: str | Path) -> dict:
    path = Path(path)
    if path.stat().st_size > MAX_BYTES:
        raise ValueError("This settings backup is too large.")
    data = json.loads(path.read_text(encoding="utf-8"))
    if (not isinstance(data, dict) or data.get("format") != FORMAT
            or data.get("version") != 1):
        raise ValueError("Choose a Quill Social settings backup. Recovered-client backups are not compatible.")
    _validate_files(data.get("files"))
    return data


def _atomic_write(path: Path, content: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=f".{path.name}-", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        Path(temporary).unlink(missing_ok=True)


def list_backups(data_dir: str | Path) -> list[Path]:
    return sorted(backup_directory(data_dir).glob("*.json"), reverse=True)


def create_backup(data_dir: str | Path, *, only_if_changed: bool = False) -> Path:
    """Save a local backup; optional deduplication suits startup/settings saves."""
    data_dir = Path(data_dir)
    files = {
        name: json.loads((data_dir / name).read_text(encoding="utf-8"))
        if (data_dir / name).exists() else None
        for name in FILES
    }
    _validate_files(files)
    if only_if_changed:
        for previous in list_backups(data_dir):
            try:
                if read_backup(previous)["files"] == files:
                    return previous
                break
            except (OSError, ValueError):
                continue
    now = datetime.now(UTC)
    path = backup_directory(data_dir) / f"{now:%Y%m%d-%H%M%S-%f}-{uuid4().hex[:8]}.json"
    payload = {"format": FORMAT, "version": 1, "created_at": now.isoformat(), "files": files}
    _atomic_write(path, json.dumps(payload, indent=2).encode("utf-8"))
    return path


def restore_backup(path: str | Path, data_dir: str | Path) -> Path:
    """Validate first, preserve a recovery backup, and roll back failed writes.

    Older backups omit global shortcuts, which are preserved when restoring.
    Each replacement is atomic. Multiple files cannot be an OS transaction;
    any write failure triggers restoration of the exact original bytes.
    """
    files = read_backup(path)["files"]
    data_dir = Path(data_dir)
    originals = {name: (data_dir / name).read_bytes() if (data_dir / name).exists()
                 else None for name in FILES}
    recovery = create_backup(data_dir)
    changed = []
    try:
        for name, values in files.items():
            target = data_dir / name
            if values is None:
                target.unlink(missing_ok=True)
            else:
                _atomic_write(target, json.dumps(values, indent=2).encode("utf-8"))
            changed.append(name)
    except OSError:
        for name in reversed(changed):
            original = originals[name]
            if original is None:
                (data_dir / name).unlink(missing_ok=True)
            else:
                _atomic_write(data_dir / name, original)
        raise
    return recovery
