from __future__ import annotations

import pytest

from quill_social.navigation import ordered_scopes, sanitize_navigation


def test_navigation_sanitizes_nested_values_and_keeps_home_available():
    assert sanitize_navigation([]) == {}
    result = sanitize_navigation({"a": {"order": ["x", 2, "x"], "labels": {"x": "  Custom  ", "z": 8}}})
    assert result == {"a": {"order": ["x"], "labels": {"x": "Custom"}}}
    assert ordered_scopes({"order": ["b", "bogus"], "hidden": ["home:all", "a"]},
                          ["home:all", "a", "b"]) == ["b", "home:all"]


@pytest.fixture
def app(tmp_path, monkeypatch):
    wx = pytest.importorskip("wx")
    monkeypatch.setenv("QUILLSOCIAL_DATA", str(tmp_path))
    application = wx.App()
    yield application
    application.Destroy()


def test_preferences_preserve_checks_when_moving_and_isolate_accounts(app):
    import wx

    from quill_social.model import Account
    from quill_social.ui.navigation_preferences import NavigationPreferencesPanel

    window = wx.Frame(None)
    try:
        panel = NavigationPreferencesPanel(window, {}, [Account(account_id="a", display_name="First")], "a")
        panel.timelines.SetSelection(1)
        moved = panel.order[1]
        panel.timelines.Check(1, False)
        panel._move(panel.timelines, panel.order, -1)
        assert panel.order[0] == moved
        assert not panel.timelines.IsChecked(0)
        panel.fields.SetCheckedItems([1])
        chosen = panel.field_order[1]
        panel.fields.SetSelection(1)
        panel._move(panel.fields, panel.field_order, -1)
        panel.sources.SetCheckedItems([])
        values = panel.get_value()
        assert values["a"]["fields"] == [chosen]
        assert values["a"]["hidden"] == [moved]
        assert values["a"]["unified_sources"] == []
        panel.account.SetSelection(0)
        panel._account_changed(None)
        assert panel.timelines.IsChecked(0)
        panel._restore(None)
        assert panel.get_value()["a"] == values["a"]
    finally:
        window.Destroy()


def test_runtime_layout_fields_and_home_sources(app):
    from quill_social.model import Account, SocialItem
    from quill_social.ui.app import SocialFrame

    frame = SocialFrame()
    try:
        frame.store.put_account(Account(account_id="custom", display_name="Custom"))
        frame.store.upsert_item(SocialItem(account_id="custom", remote_id="1", text="Keep", bookmarked=True))
        frame.store.upsert_item(SocialItem(account_id="custom", remote_id="2", text="Hide"))
        frame.a11y.navigation = {"custom": {"order": ["library:bookmarks"], "hidden": ["attention:mentions"],
                                            "labels": {"library:bookmarks": "Saved posts"},
                                            "fields": ["text", "author"], "unified_sources": ["library:bookmarks"]}}
        frame._populate_accounts()
        frame._select_account("custom", announce=False)
        assert frame._timeline_scopes[0] == "library:bookmarks"
        assert frame.timelines.GetString(0) == "Saved posts"
        assert "attention:mentions" not in frame._timeline_scopes
        assert "attention:mentions" not in frame._nav_nodes
        assert [item.text for item in frame._scope_items("home:all")] == ["Keep"]
        assert frame._post_profile("custom").enabled() == ["text", "author"]
        frame.a11y.navigation["custom"]["unified_sources"] = ["home:all", "library:bookmarks"]
        assert len(frame._scope_items("home:all")) == 2
        frame.a11y.navigation["custom"]["unified_sources"] = []
        assert frame._scope_items("home:all") == []
        frame.a11y.navigation.clear()
        frame._populate_nav()
        assert "attention:mentions" in frame._nav_nodes
    finally:
        frame._on_close(None)
