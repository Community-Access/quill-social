"""Native settings backup dialogs shared by both interface modes."""

from pathlib import Path

import wx

from quill_social.services.settings_backup import backup_directory, create_backup, restore_backup

CONTENTS = (
    "Backups contain Quill Social Preferences, keyboard shortcuts and global shortcuts. "
    "Older backups without global shortcuts keep your current global shortcuts. "
    "They do not include accounts, sign-in credentials, posts, drafts, "
    "scheduled posts, templates, notification policies or other database settings. "
    "Backups from the recovered client cannot be imported."
)


def create_settings_backup(parent, data_dir: str | Path) -> Path | None:
    try:
        path = create_backup(data_dir)
    except (OSError, ValueError) as exc:
        wx.MessageBox(f"Could not create a settings backup: {exc}", "Settings backup",
                      wx.OK | wx.ICON_ERROR, parent)
        return None
    wx.MessageBox(f"{CONTENTS}\n\nSaved to:\n{path}", "Settings backup created",
                  wx.OK | wx.ICON_INFORMATION, parent)
    return path


def restore_settings_backup(parent, data_dir: str | Path) -> bool:
    directory = backup_directory(data_dir)
    with wx.FileDialog(parent, "Restore Settings Backup", defaultDir=str(directory),
                       wildcard="Quill Social settings backups (*.json)|*.json",
                       style=wx.FD_OPEN | wx.FD_FILE_MUST_EXIST) as dialog:
        if dialog.ShowModal() != wx.ID_OK:
            return False
        path = dialog.GetPath()
    from quill_social.services.settings_backup import read_backup
    try:
        read_backup(path)
    except (OSError, ValueError) as exc:
        wx.MessageBox(str(exc), "Cannot restore settings backup", wx.OK | wx.ICON_ERROR, parent)
        return False
    if wx.MessageBox(
        f"{CONTENTS}\n\nReplace the Preferences and shortcuts included in this backup? "
        "A recovery backup of your current settings will be saved first.",
        "Restore Settings Backup", wx.YES_NO | wx.NO_DEFAULT | wx.ICON_QUESTION, parent,
    ) != wx.YES:
        return False
    try:
        recovery = restore_backup(path, data_dir)
    except (OSError, ValueError) as exc:
        wx.MessageBox(f"Could not restore the settings backup: {exc}", "Settings backup",
                      wx.OK | wx.ICON_ERROR, parent)
        return False
    wx.MessageBox(f"Preferences and keyboard shortcuts restored.\n\nRecovery backup:\n{recovery}",
                  "Settings restored", wx.OK | wx.ICON_INFORMATION, parent)
    return True
