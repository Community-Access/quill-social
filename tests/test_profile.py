from types import SimpleNamespace

import pytest

from quill_social.adapters.base import AdapterError
from quill_social.adapters.bluesky import BlueskyAdapter
from quill_social.adapters.mastodon import MastodonAdapter
from quill_social.adapters.mock import MockNetwork


def test_mastodon_profile_uses_source_text_and_sends_only_changes():
    calls = []
    client = SimpleNamespace(
        account_verify_credentials=lambda: {
            "display_name": "Alex", "note": "<p>Rendered</p>", "locked": True,
            "source": {"note": "Original **text**", "fields": [{"name": "Site", "value": "https://example.org"}]},
        }, account_update_credentials=lambda **kw: calls.append(kw),
    )
    adapter = MastodonAdapter(client=client)
    result = adapter.own_profile()
    assert result["note"] == "Original **text**"
    assert result["fields"] == [("Site", "https://example.org")]
    assert "discoverable" not in result
    adapter.update_profile({"note": "", "unrecognized": "ignored"})
    assert calls == [{"note": ""}]


def test_bluesky_profile_preserves_unedited_record_and_uses_cid():
    calls = []
    record = {"$type": "app.bsky.actor.profile", "displayName": "Alex",
              "description": "Old", "avatar": {"ref": "keep"}, "futureField": True}
    repo = SimpleNamespace(
        get_record=lambda params: {"value": record, "cid": "old-cid"},
        put_record=lambda data: calls.append(data),
    )
    adapter = BlueskyAdapter(did="did:plc:alex", client=SimpleNamespace(
        com=SimpleNamespace(atproto=SimpleNamespace(repo=repo))))
    assert adapter.own_profile() == {"display_name": "Alex", "note": "Old"}
    adapter.update_profile({"note": "New"})
    assert calls[0]["record"] == {**record, "description": "New"}
    assert calls[0]["swap_record"] == "old-cid"
    assert record["description"] == "Old"


def test_unavailable_profiles_explain_failure():
    with pytest.raises(AdapterError, match="not supported"):
        MockNetwork().own_profile()
    with pytest.raises(AdapterError, match="not enabled"):
        MastodonAdapter().own_profile()


def test_profile_dialog_changed_fields_and_failed_save_preserve_edits(monkeypatch):
    wx = pytest.importorskip("wx")
    from quill_social.ui.profile import ProfileDialog
    app = wx.App()
    monkeypatch.setattr(ProfileDialog, "_run", lambda *args: None)
    monkeypatch.setattr(wx, "MessageBox", lambda *args: None)
    dialog = ProfileDialog(None, lambda: None)
    try:
        adapter = SimpleNamespace(name="mastodon")
        dialog._loaded((adapter, {"display_name": "Alex", "note": "Bio", "fields": [], "locked": True}), None)
        assert dialog._changes() == {}
        dialog.controls["note"].SetValue("")
        assert dialog._changes() == {"note": ""}
        dialog._on_save(None)
        assert not dialog.save.IsEnabled()
        dialog._saved(None, "Server rejected the change")
        assert dialog.save.IsEnabled()
        assert dialog.controls["note"].GetValue() == ""
    finally:
        dialog.Destroy()
        app.Destroy()
