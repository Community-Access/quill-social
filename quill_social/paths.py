"""Platform data-directory resolution for QUILL Social (wx-free).

A single source of truth for where the local store and per-user files live, so
the GUI (``ui/app.py``), the headless CLI (``cli.py``), and the services all
agree. Importing this module must not pull in wx.

Override with the ``QUILLSOCIAL_DATA`` (or ``QUILL_APP_ROOT``) environment
variable; otherwise the platform's per-user application-data directory is used.
Mirrors ``quill_beacon.paths`` and ``quill.core.paths``.
"""

from __future__ import annotations

import json
import os
import shutil
import sqlite3
import sys
import tempfile
from contextlib import closing
from pathlib import Path

APP_DIRNAME = "QuillSocial"


def default_data_dir() -> Path:
    """Default location, without creating it or consulting a saved override."""
    if sys.platform.startswith("win"):
        p = Path(os.environ.get("APPDATA", str(Path.home()))) / APP_DIRNAME
    elif sys.platform == "darwin":
        p = Path.home() / "Library" / "Application Support" / APP_DIRNAME
    else:
        p = Path.home() / ".local" / "share" / APP_DIRNAME
    return p


def environment_override() -> bool:
    return bool(os.environ.get("QUILLSOCIAL_DATA") or os.environ.get("QUILL_APP_ROOT"))


def data_dir() -> Path:
    """Resolve the environment override, saved location, or platform default."""
    base = os.environ.get("QUILLSOCIAL_DATA") or os.environ.get("QUILL_APP_ROOT")
    p = Path(base) / APP_DIRNAME if base else default_data_dir()
    if not base:
        pointer = p / "data-location.json"
        if pointer.exists():
            # An invalid/unavailable override must not silently open an empty account store.
            value = json.loads(pointer.read_text(encoding="utf-8"))["data_folder"]
            p = Path(value)
            if not p.is_absolute() or not p.is_dir():
                raise OSError(f"The configured Quill Social data folder is unavailable: {p}")
    p.mkdir(parents=True, exist_ok=True)
    return p


def validate_data_location(source: Path, destination: Path) -> Path:
    source, destination = Path(source).resolve(), Path(destination).expanduser().resolve()
    if destination == source:
        return destination
    if destination.is_relative_to(source) or source.is_relative_to(destination):
        raise ValueError("Choose a folder outside the current data folder and its parents.")
    if destination.exists() and (
        not destination.is_dir() or
        any(p.name != "data-location.json" for p in destination.iterdir())
    ):
        raise ValueError("Choose an empty folder so existing data will not be overwritten.")
    return destination


def migrate_data_location(source: Path, destination: Path) -> Path:
    """Copy data after writers have stopped, then atomically switch the pointer.

    The source is retained. SQLite backup includes WAL contents. Credential references
    remain identical; operating-system credentials are not exported or changed.
    On failure the pointer remains unchanged and the staged copy is retained.
    """
    if environment_override():
        raise ValueError("The data folder is controlled by an environment variable.")
    source = Path(source).resolve()
    destination = validate_data_location(source, destination)
    if destination == source:
        return source
    destination.parent.mkdir(parents=True, exist_ok=True)
    stage = Path(tempfile.mkdtemp(prefix=".quill-social-copy-", dir=destination.parent))
    for entry in source.iterdir():
        if entry.name in {"data-location.json", "social.db-wal", "social.db-shm"}:
            continue
        target = stage / entry.name
        if entry.name == "social.db":
            with closing(sqlite3.connect(entry.as_uri() + "?mode=ro", uri=True)) as src:
                with closing(sqlite3.connect(target)) as dst:
                    src.backup(dst)
        elif entry.is_dir():
            shutil.copytree(entry, target)
        else:
            shutil.copy2(entry, target)
    # Recheck after copying in case another process created files in the destination.
    validate_data_location(source, destination)
    destination.mkdir(parents=True, exist_ok=True)
    for entry in stage.iterdir():
        entry.rename(destination / entry.name)
    stage.rmdir()
    root = default_data_dir()
    root.mkdir(parents=True, exist_ok=True)
    pointer = root / "data-location.json"
    temporary = root / "data-location.json.tmp"
    temporary.write_text(json.dumps({"data_folder": str(destination)}, indent=2), encoding="utf-8")
    temporary.replace(pointer)
    return destination


def db_path() -> Path:
    """Path to the primary SQLite store."""
    return data_dir() / "social.db"
