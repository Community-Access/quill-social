"""Native shortcut preferences."""

from __future__ import annotations

import json
from pathlib import Path

import wx

from quill_social import keymap as keymap_mod

GLOBAL_BINDINGS_NAME = "global-shortcuts.json"
COMMAND_LABELS = {
    "compose": "New post", "reply": "Reply", "repost": "Boost / repost",
    "quote": "Quote post", "favourite": "Favourite", "bookmark": "Bookmark",
    "open_conversation": "Open conversation", "open_links": "Open links",
    "play_media": "Open media", "command_center": "Command center",
    "where_am_i": "Where am I", "next_pane": "Next pane", "prev_pane": "Previous pane",
    "refresh": "Refresh", "mark_read": "Mark read", "search": "Search", "help": "Help",
    **{f"goto_{n}": f"Go to view {n}" for n in range(1, 10)},
}
GLOBAL_LABELS = {"show_window": "Show Quill Social", "compose": "New post", "refresh": "Refresh"}
COMMAND_LABELS["repeat_announcement"] = "Repeat last announcement"


def load_global_bindings(data_dir: Path) -> dict[str, str]:
    try:
        saved = json.loads((Path(data_dir) / GLOBAL_BINDINGS_NAME).read_text(encoding="utf-8"))
        return {key: keymap_mod.validate_chord(value, global_hotkey=True)
                for key, value in saved.items() if key in GLOBAL_LABELS and isinstance(value, str)}
    except (OSError, ValueError, AttributeError):
        return {}


def save_global_bindings(data_dir: Path, bindings: dict[str, str]) -> None:
    (Path(data_dir) / GLOBAL_BINDINGS_NAME).write_text(json.dumps(bindings, indent=2), encoding="utf-8")


class ShortcutsPanel(wx.Panel):
    def __init__(self, parent, keymap, global_bindings=None):
        super().__init__(parent)
        self.keymap = keymap_mod.Keymap(keymap.as_dict())
        self.global_bindings = dict(global_bindings or {})
        self.entries = [(False, key, name) for key, name in COMMAND_LABELS.items()]
        self.entries += [(True, key, name) for key, name in GLOBAL_LABELS.items()]
        sizer = wx.BoxSizer(wx.VERTICAL)
        sizer.Add(wx.StaticText(self, label="Select a command, then type a shortcut such as Ctrl+Shift+N.\n"
                               "Global shortcuts work while another application has focus."), 0, wx.ALL, 6)
        self.commands = wx.ListBox(self, name="Keyboard shortcuts")
        sizer.Add(self.commands, 1, wx.EXPAND | wx.ALL, 6)
        sizer.Add(wx.StaticText(self, label="&Shortcut:"), 0, wx.LEFT, 6)
        self.shortcut = wx.TextCtrl(self, name="Shortcut")
        sizer.Add(self.shortcut, 0, wx.EXPAND | wx.ALL, 6)
        row = wx.BoxSizer(wx.HORIZONTAL)
        for label, handler in (("&Assign", self._assign), ("&Clear", self._clear),
                               ("Reset &all shortcuts", self._reset)):
            button = wx.Button(self, label=label)
            button.Bind(wx.EVT_BUTTON, handler)
            row.Add(button, 0, wx.ALL, 4)
        sizer.Add(row)
        self.SetSizer(sizer)
        self.commands.Bind(wx.EVT_LISTBOX, self._select)
        self._refresh(0)

    def _refresh(self, selection):
        self.commands.Set([
            f"{'Global: ' if global_key else ''}{name}: "
            f"{(self.global_bindings.get(key, '') if global_key else self.keymap.chord_for(key)) or 'Unassigned'}"
            for global_key, key, name in self.entries])
        self.commands.SetSelection(selection)
        self._select(None)

    def _select(self, _event):
        index = self.commands.GetSelection()
        if index != wx.NOT_FOUND:
            global_key, key, _name = self.entries[index]
            self.shortcut.ChangeValue(self.global_bindings.get(key, "") if global_key
                                      else self.keymap.chord_for(key))

    def _assign(self, _event):
        index = self.commands.GetSelection()
        if index == wx.NOT_FOUND:
            return
        global_key, key, _name = self.entries[index]
        try:
            chord = keymap_mod.validate_chord(self.shortcut.GetValue(), global_hotkey=global_key)
            other = self.keymap.command_for(chord) if global_key else next(
                (command for command, value in self.global_bindings.items() if value and value == chord), None)
            if other:
                raise ValueError(f"{chord} is already assigned to {other.replace('_', ' ')} in the other shortcut group.")
            if global_key:
                owner = next((command for command, value in self.global_bindings.items()
                              if value and value == chord and command != key), None)
                if owner:
                    raise ValueError(f"{chord} is already assigned to {GLOBAL_LABELS[owner]}.")
                self.global_bindings[key] = chord
            else:
                self.keymap.rebind(key, chord)
        except ValueError as exc:
            wx.MessageBox(str(exc), "Shortcut unavailable", wx.OK | wx.ICON_WARNING, self)
            self.shortcut.SetFocus()
            return
        self._refresh(index)
        self.commands.SetFocus()

    def _clear(self, event):
        self.shortcut.ChangeValue("")
        self._assign(event)

    def _reset(self, _event):
        self.keymap = keymap_mod.Keymap()
        self.global_bindings = {}
        self._refresh(max(0, self.commands.GetSelection()))

    def collect(self):
        return self.keymap, dict(self.global_bindings)


class GlobalHotkeys:
    """Register explicitly chosen hotkeys; report every OS reservation failure."""

    def __init__(self, frame):
        self.frame = frame
        self.registered = {}
        self._ids = []
        frame.Bind(wx.EVT_HOTKEY, self._on_hotkey)

    def apply(self, bindings):
        self.close()
        errors = []
        names = keymap_mod._build_keycode_names()
        keycodes = {name: code for code, name in names.items()}
        keycodes["Enter"] = wx.WXK_RETURN
        for command, chord in bindings.items():
            if not chord or command not in GLOBAL_LABELS:
                continue
            try:
                chord = keymap_mod.validate_chord(chord, global_hotkey=True)
                parts = chord.split("+")
                flags = 0
                for modifier, flag in (("Ctrl", wx.MOD_CONTROL), ("Alt", wx.MOD_ALT), ("Shift", wx.MOD_SHIFT)):
                    if modifier in parts[:-1]:
                        flags |= flag
                code = keycodes.get(parts[-1])
                if code is None:
                    code = ord(parts[-1])
                reference = wx.NewIdRef()
                identifier = int(reference)
                if not self.frame.RegisterHotKey(identifier, flags, code):
                    raise ValueError("already in use or unsupported by this desktop")
                self.registered[identifier] = command
                self._ids.append(reference)
            except (ValueError, RuntimeError, NotImplementedError) as exc:
                errors.append(f"{GLOBAL_LABELS[command]} ({chord}): {exc}")
        return errors

    def close(self):
        for identifier in self.registered:
            self.frame.UnregisterHotKey(identifier)
        self.registered.clear()
        self._ids.clear()

    def _on_hotkey(self, event):
        command = self.registered.get(event.GetId())
        if command is None or not self.frame.IsEnabled():
            return
        if command in {"show_window", "compose"}:
            tray = getattr(self.frame, "_tray", None)
            if tray and (not self.frame.IsShown() or self.frame.IsIconized()):
                tray.restore()
            else:
                self.frame.Show()
                self.frame.Iconize(False)
                self.frame.Raise()
        if command != "show_window":
            self.frame._dispatch(command)
