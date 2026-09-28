"""Behavioral coverage of recovered reading, cache and fetch preferences."""

from types import SimpleNamespace

import pytest

from quill_social.a11y import A11ySettings
from quill_social.adapters.bluesky import BlueskyAdapter
from quill_social.adapters.mastodon import MastodonAdapter
from quill_social.fields import field_value
from quill_social.model import Account, Draft, Note, SocialItem
from quill_social.reading_options import automatic_text, sanitize_account_options
from quill_social.services import refresh


def test_malformed_settings_preserve_safe_defaults_and_valid_account_values():
    settings = A11ySettings.from_dict({
        "timeline_limit": True, "timeline_display_limit": -1, "navigation": [],
        "account_options": {"good": {"content_warning_mode": "warning_only", "speech_timelines": ["bogus", "home:all"]},
                            "bad": {"speech_timelines": 3, "sync_home_position": "false"}, "skip": None},
    })
    assert settings.timeline_limit == 60
    assert settings.timeline_display_limit == 500
    assert settings.navigation == {}
    assert settings.account_options["good"]["speech_timelines"] == ["home:all"]
    assert settings.account_options["bad"]["sync_home_position"] is False
    assert settings.account_options["bad"]["speech_timelines"] == []
    assert "skip" not in settings.account_options
    assert sanitize_account_options(None) == {}
    assert A11ySettings.from_dict(settings.to_dict()) == settings


@pytest.mark.parametrize("mode, text, warning", [
    ("warning_only", "", "Sensitive café"),
    ("text_only", "The café is open. Second sentence.", ""),
    ("warning_then_text", "The café is open. Second sentence.", "Sensitive café"),
])
def test_warning_modes_and_unicode_are_presentation_only(mode, text, warning):
    item = SocialItem(account_id="a", author_display="Renée", text="The café is open. Second sentence.",
                      content_warning="Sensitive café")
    settings = A11ySettings(account_options={"a": {"content_warning_mode": mode}})
    assert field_value(item, "text", settings=settings) == text
    assert field_value(item, "content_warning", settings=settings) == (f"content warning: {warning}" if warning else "")
    settings.remove_unicode = True
    assert field_value(item, "author", settings=settings) == "Renee"
    assert "é" not in field_value(item, "text", settings=settings)
    assert "é" not in field_value(item, "content_warning", settings=settings)
    assert item.author_display == "Renée"
    assert item.text == "The café is open. Second sentence."


def test_automatic_notification_text_respects_reading_options():
    item = SocialItem(account_id="a", author_display="Renée", text="A café https://example.test is open. Later.",
                      content_warning="Sensitive")
    settings = A11ySettings(account_options={"a": {"content_warning_mode": "text_only"}},
                            exclude_web_addresses=True, notification_first_sentence=True, remove_unicode=True)
    spoken = automatic_text(item, settings, scope="attention:notifications")
    assert "Renee:" in spoken and "cafe" in spoken
    assert "Later" not in spoken and "https" not in spoken and "Sensitive" not in spoken
    settings.account_options["a"]["content_warning_mode"] = "warning_only"
    assert automatic_text(item, settings, scope="attention:notifications") == "New notification from Renee: Content warning: Sensitive"


def test_clear_cache_preserves_saved_posts_local_work_other_accounts_and_drafts(store):
    store.upsert_item(SocialItem(account_id="a", remote_id="disposable", text="Disposable needle"))
    protected = [SocialItem(account_id="a", remote_id=str(i), text="Saved needle", **flags)
                 for i, flags in enumerate([{"bookmarked": True}, {"flagged": True}, {"favourited": True},
                                             {"tags": ["work"]}, {"folders": ["saved"]}])]
    protected.append(SocialItem(account_id="a", remote_id="note", text="Noted needle"))
    protected.append(SocialItem(account_id="b", remote_id="other", text="Other account needle"))
    for item in protected:
        store.upsert_item(item)
    store.put_note(Note(target_type="post", target_id=protected[-2].item_id, text="Do not lose this"))
    draft = store.put_draft(Draft(text="Work in progress"))
    assert store.clear_timeline_cache("a") == 1
    assert {item.item_id for item in store.list_items()} == {item.item_id for item in protected}
    assert len(store.search_items("needle")) == len(protected)
    assert store.get_draft(draft.draft_id).text == "Work in progress"
    assert store.clear_timeline_cache("a") == 0


def test_refresh_passes_limit_records_notification_keys_and_syncs_only_enabled_mastodon(monkeypatch):
    calls = []

    class Fake:
        def __init__(self, account):
            self.account = account

        def home_timeline(self, *, limit):
            calls.append((self.account.account_id, "home", limit))
            return [SocialItem(remote_id="home")]

        def notifications(self, *, limit):
            return [SocialItem(remote_id="note")]

        def home_position(self):
            calls.append((self.account.account_id, "marker"))
            return "123"

    monkeypatch.setattr(refresh, "adapter_for", lambda account, credentials: Fake(account))
    accounts = [Account(account_id="m", network="mastodon"), Account(account_id="b", network="bluesky"),
                Account(account_id="off", network="mastodon")]
    result = refresh.fetch_accounts(accounts, None, limit=137, sync_accounts=["m", "b"])
    assert result.home_positions == {"m": "123"}
    assert result.notification_keys == {("m", "note"), ("b", "note"), ("off", "note")}
    assert result.posts == 3 and len(result.items) == 6 and not result.errors
    assert [call for call in calls if len(call) == 3] == [(a.account_id, "home", 137) for a in accounts]


def test_mastodon_pages_with_server_cap_and_marker_round_trip():
    calls = []
    marker = {"home": {"last_read_id": "77"}}

    def timeline_home(**kwargs):
        calls.append(kwargs)
        start = int(kwargs.get("max_id", "101")) - 1
        return [{"id": str(i), "content": "post", "account": {}} for i in range(start, start - kwargs["limit"], -1)]

    def markers_set(timeline, remote_id):
        marker[timeline]["last_read_id"] = remote_id

    adapter = MastodonAdapter(account_id="a", client=SimpleNamespace(
        timeline_home=timeline_home, markers_get=lambda timeline: marker, markers_set=markers_set))
    items = adapter.home_timeline(limit=95, since_id="2")
    assert len(items) == 95 and len({item.remote_id for item in items}) == 95
    assert [call["limit"] for call in calls] == [40, 40, 15]
    assert [call.get("max_id") for call in calls] == [None, "61", "21"]
    assert all(call["since_id"] == "2" for call in calls)
    assert adapter.home_position() == "77"
    adapter.save_home_position("88")
    assert adapter.home_position() == "88"


def test_bluesky_pages_cursor_and_stops_on_repeated_cursor():
    calls = []

    def get_timeline(**kwargs):
        calls.append(kwargs)
        base = 0 if "cursor" not in kwargs else 100
        return {"feed": [{"post": {"uri": f"at://did/app.bsky.feed.post/{i}", "record": {"text": "post"},
                                   "author": {}}} for i in range(base, base + kwargs["limit"])], "cursor": "next"}

    adapter = BlueskyAdapter(client=SimpleNamespace(get_timeline=get_timeline))
    assert len(adapter.home_timeline(limit=250)) == 200  # A repeated cursor must not loop forever.
    assert calls == [{"limit": 100}, {"limit": 100, "cursor": "next"}]
