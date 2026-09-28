"""Native menu accelerators follow remapped and cleared keyboard shortcuts."""

import pytest

wx = pytest.importorskip("wx")

from quill_social.keymap import Keymap  # noqa: E402
from quill_social.ui.app import SocialFrame  # noqa: E402


@pytest.fixture
def app():
    try:
        application = wx.App()
    except Exception:
        pytest.skip("wx cannot initialize")
    yield application
    application.Destroy()


def test_menu_uses_remapped_shortcut_and_clears_old_accelerator(app):
    frame = wx.Frame(None)
    frame.keymap = Keymap()
    frame._menu_bindings = []
    menu = wx.Menu()
    try:
        frame.keymap.rebind("compose", "Ctrl+Shift+N")
        SocialFrame._menu_item(frame, menu, "&New Post\tCtrl+N", lambda event: None)
        item = menu.GetMenuItems()[0]
        assert item.GetItemLabel() == "&New Post\tCtrl+Shift+N"
        assert item.GetAccel().GetFlags() == wx.ACCEL_CTRL | wx.ACCEL_SHIFT
        frame.keymap.rebind("compose", "")
        SocialFrame._menu_item(frame, menu, "&New Post\tCtrl+N", lambda event: None)
        cleared = menu.GetMenuItems()[1]
        assert cleared.GetItemLabel() == "&New Post"
        assert cleared.GetAccel() is None
    finally:
        menu.Destroy()
        frame.Destroy()


def test_all_remappable_menu_accelerators_follow_bindings(app):
    frame = wx.Frame(None)
    frame.keymap = Keymap()
    frame._menu_bindings = []
    menu = wx.Menu()
    try:
        frame.keymap.rebind("reply", "Alt+R")
        frame.keymap.rebind("refresh", "Ctrl+F5")
        SocialFrame._menu_item(frame, menu, "&Reply\tCtrl+R", lambda event: None)
        SocialFrame._menu_item(frame, menu, "&Refresh\tF5", lambda event: None)
        assert [item.GetItemLabel() for item in menu.GetMenuItems()] == ["&Reply\tAlt+R", "&Refresh\tCtrl+F5"]
    finally:
        menu.Destroy()
        frame.Destroy()
