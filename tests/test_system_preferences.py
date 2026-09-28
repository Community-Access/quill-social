"""Shortcut edits are staged, conflict-safe, and report OS failures."""

import pytest

wx = pytest.importorskip("wx")

from quill_social.keymap import Keymap  # noqa: E402
from quill_social.ui import system_preferences as prefs  # noqa: E402


@pytest.fixture
def app():
    try:
        application = wx.App()
    except Exception:
        pytest.skip("wx cannot initialize")
    yield application
    application.Destroy()


def test_shortcut_clear_reset_and_cancel_are_staged(app):
    frame = wx.Frame(None)
    original = Keymap()
    panel = prefs.ShortcutsPanel(frame, original)
    try:
        panel.commands.SetSelection(0)
        panel._clear(None)
        assert panel.collect()[0].chord_for("compose") == ""
        assert original.chord_for("compose") == "Ctrl+N"
        panel._reset(None)
        assert panel.collect()[0].chord_for("compose") == "Ctrl+N"
    finally:
        frame.Destroy()


def test_shortcut_conflicts_keep_existing_binding(app, monkeypatch):
    frame = wx.Frame(None)
    panel = prefs.ShortcutsPanel(frame, Keymap())
    messages = []
    monkeypatch.setattr(wx, "MessageBox", lambda message, *args: messages.append(message))
    try:
        panel.commands.SetSelection(0)
        panel.shortcut.SetValue("Ctrl+R")
        panel._assign(None)
        assert messages and "reply" in messages[-1]
        assert panel.collect()[0].chord_for("compose") == "Ctrl+N"
        panel.commands.SetSelection(len(prefs.COMMAND_LABELS))
        panel.shortcut.SetValue("Ctrl+R")
        panel._assign(None)
        assert "other shortcut group" in messages[-1]
        assert panel.collect()[1] == {}
    finally:
        frame.Destroy()


def test_global_bindings_persist(tmp_path):
    bindings = {"show_window": "Ctrl+Alt+Q", "compose": ""}
    prefs.save_global_bindings(tmp_path, bindings)
    assert prefs.load_global_bindings(tmp_path) == bindings


def test_global_registration_reports_failure_and_unregisters(app):
    class Frame:
        def __init__(self):
            self.active = set()
            self.calls = []
        def Bind(self, *args):
            pass
        def RegisterHotKey(self, identifier, flags, code):
            if code == ord("Q"):
                return False
            self.active.add(identifier)
            return True
        def UnregisterHotKey(self, identifier):
            self.active.remove(identifier)
        def IsEnabled(self):
            return True
        def _dispatch(self, command):
            self.calls.append(command)
    frame = Frame()
    hotkeys = prefs.GlobalHotkeys(frame)
    errors = hotkeys.apply({"show_window": "Ctrl+Alt+Q", "refresh": "Ctrl+Alt+R"})
    assert len(errors) == 1 and "Show Quill Social" in errors[0]
    identifier = next(iter(frame.active))
    class Event:
        def GetId(self):
            return identifier
    hotkeys._on_hotkey(Event())
    assert frame.calls == ["refresh"]
    hotkeys.close()
    assert frame.active == set()
