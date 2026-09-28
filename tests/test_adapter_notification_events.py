from types import SimpleNamespace

import pytest

from quill_social.adapters.base import AdapterError
from quill_social.adapters.bluesky import BlueskyAdapter
from quill_social.adapters.mastodon import MastodonAdapter


def test_mastodon_notification_identity_actor_and_statusless_follow():
    status = {"id": "post", "content": "<p>My post</p>",
              "account": {"acct": "me", "display_name": "Me"}}
    notes = [
        {"id": "1", "type": "favourite", "status": status,
         "account": {"acct": "ada", "display_name": "Ada"}},
        {"id": "2", "type": "reblog", "status": status,
         "account": {"acct": "ben", "display_name": "Ben"}},
        {"id": "3", "type": "follow", "account": {"acct": "cat"}},
        {"id": "4", "type": "mention", "status": status,
         "account": {"acct": "ada"}},
    ]
    adapter = MastodonAdapter(account_id="mine", client=SimpleNamespace(
        notifications=lambda **kw: notes))
    events = adapter.notification_events()
    assert [event.notification_id for event in events] == ["1", "2", "3", "4"]
    assert [event.kind for event in events] == ["favourite", "reblog", "follow", "mention"]
    assert events[0].actor_name == "Ada"
    assert events[0].item.author_display == "Me"
    assert events[0].item.remote_id == events[1].item.remote_id
    assert events[2].item is None
    assert events[2].actor_handle == "@cat"
    assert all(event.account_id == "mine" for event in events)
    assert len(adapter.notifications()) == 3


def test_mastodon_statusless_moderation_notification_keeps_its_text():
    notes = [{
        "id": "warning", "type": "moderation_warning",
        "created_at": "2026-09-20T10:00:00Z",
        "account": {},
        "moderation_warning": {"action": "disable", "text": "Please review our rules."},
    }]
    event = MastodonAdapter(account_id="mine", client=SimpleNamespace(
        notifications=lambda **kw: notes)).notification_events()[0]
    assert event.item is None
    assert event.text == "Please review our rules."


def test_bluesky_notifications_use_notification_endpoint_and_keep_reactions():
    subject = "at://me/app.bsky.feed.post/original"
    actor = {"handle": "ada.bsky.social", "display_name": "Ada"}
    notes = [
        {"uri": "like1", "reason": "like", "reason_subject": subject, "author": actor},
        {"uri": "repost1", "reason": "repost", "reason_subject": subject, "author": actor},
        {"uri": "follow1", "reason": "follow", "author": actor},
        {"uri": "mention1", "reason": "mention", "author": actor,
         "record": {"text": "Hello @me", "created_at": "2026-09-20T10:00:00Z"}},
    ]
    calls = []

    def notifications(params):
        calls.append(params)
        return SimpleNamespace(notifications=notes)

    def posts(uris):
        assert uris == [subject]
        return SimpleNamespace(posts=[{
            "uri": subject, "author": {"display_name": "Me"}, "record": {"text": "My post"},
        }])

    client = SimpleNamespace(app=SimpleNamespace(bsky=SimpleNamespace(
        notification=SimpleNamespace(list_notifications=notifications))), get_posts=posts)
    events = BlueskyAdapter(client=client, account_id="mine").notification_events(limit=7)
    assert calls == [{"limit": 7}]
    assert [event.notification_id for event in events] == ["like1", "repost1", "follow1", "mention1"]
    assert events[0].actor_name == "Ada"
    assert events[0].item.author_display == "Me"
    assert events[0].item.remote_id == events[1].item.remote_id == subject
    assert events[2].kind == "follow" and events[2].item is None
    assert events[3].item.text == "Hello @me"
    assert events[3].item.author_handle == "@ada.bsky.social"


@pytest.mark.parametrize("method,field,off_method", [
    ("set_favourite", "like", "unlike"), ("set_reblog", "repost", "unrepost"),
])
def test_bluesky_remove_interaction_uses_viewer_record_uri(method, field, off_method):
    post_uri = "at://author/app.bsky.feed.post/post"
    record_uri = f"at://me/app.bsky.feed.{field}/reaction"
    calls = []
    client = SimpleNamespace(get_posts=lambda uris: {"posts": [
        {"uri": post_uri, "viewer": {field: record_uri}},
    ]}, **{off_method: lambda uri: calls.append(uri)})
    getattr(BlueskyAdapter(client=client), method)(post_uri, False)
    assert calls == [record_uri]


def test_bluesky_failed_unlike_does_not_report_success():
    client = SimpleNamespace(get_posts=lambda uris: {"posts": [
        {"uri": "post", "viewer": {"like": "reaction"}},
    ]}, unlike=lambda uri: False)
    with pytest.raises(AdapterError, match="could not be removed"):
        BlueskyAdapter(client=client).set_favourite("post", False)
