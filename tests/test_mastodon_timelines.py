from unittest.mock import Mock

import pytest

from quill_social.adapters.base import AdapterError, PublishRequest
from quill_social.adapters.mastodon import MastodonAdapter, _status_to_item


def status(id="1", visibility="public"):
    return {"id": id, "visibility": visibility, "content": "Hello", "account": {"acct": "ada"}}


@pytest.mark.parametrize("kind,value,method,args", [
    ("list", "9", "timeline_list", ("9",)),
    ("hashtag", "#accessibility", "timeline_hashtag", ("accessibility",)),
    ("local", "", "timeline_local", ()),
    ("user", "@ada@example.social", "account_statuses", ("7",)),
])
def test_additional_timelines(kind, value, method, args):
    client = Mock()
    client.account_lookup.return_value = {"id": "7"}
    getattr(client, method).return_value = [status()]
    rows = MastodonAdapter(client=client, account_id="own").fetch_timeline(kind, value, limit=12)
    getattr(client, method).assert_called_once_with(*args, limit=12)
    assert rows[0].account_id == "own"
    if kind == "user":
        client.account_lookup.assert_called_once_with("ada@example.social")


def test_messages_only_return_direct_conversation_statuses():
    client = Mock()
    client.conversations.return_value = [
        {"last_status": status("1", "direct")}, {"last_status": None},
        {"last_status": status("2", "public")},
    ]
    rows = MastodonAdapter(client=client).fetch_timeline("messages")
    assert [row.remote_id for row in rows] == ["1"]
    assert rows[0].visibility == "direct"


def test_lists_and_errors():
    client = Mock()
    client.lists.return_value = [{"id": 4, "title": "Friends"}]
    adapter = MastodonAdapter(client=client)
    assert adapter.timeline_lists() == [("4", "Friends")]
    client.timeline_local.side_effect = RuntimeError("offline")
    with pytest.raises(AdapterError):
        adapter.fetch_timeline("local")


def test_search_returns_posts_users_and_hashtags():
    client = Mock()
    client.search_v2.return_value = {
        "statuses": [status("post")],
        "accounts": [{"id": "7", "acct": "ada@example.social",
                      "display_name": "Ada", "note": "<p>Profile</p>",
                      "url": "https://example.social/@ada"}],
        "hashtags": [{"name": "Accessibility", "url": "https://example.social/tags/accessibility"}],
    }
    rows = MastodonAdapter(client=client, account_id="own").search("accessibility", limit=12)
    assert [row.remote_id for row in rows] == [
        "post", "search:user:7", "search:hashtag:accessibility"]
    assert rows[1].text == "User: Ada @ada@example.social. Profile"
    client.search_v2.assert_called_once_with(
        "accessibility", resolve=True, result_type=None, limit=12)


def test_search_type_is_forwarded_and_validated():
    client = Mock()
    client.search_v2.return_value = {"accounts": []}
    MastodonAdapter(client=client).search("ada", "accounts")
    assert client.search_v2.call_args.kwargs["result_type"] == "accounts"
    with pytest.raises(AdapterError):
        MastodonAdapter(client=client).search("", "statuses")


def test_remote_instance_uses_separate_unauthenticated_client(monkeypatch):
    public = Mock()
    public.timeline_local.return_value = [status()]
    constructor = Mock(return_value=public)
    monkeypatch.setattr("mastodon.Mastodon", constructor)
    authenticated = Mock()
    adapter = MastodonAdapter(client=authenticated, access_token="secret", account_id="own")
    rows = adapter.fetch_timeline("instance", "other.social")
    constructor.assert_called_once_with(api_base_url="https://other.social", request_timeout=30)
    assert not authenticated.mock_calls
    assert rows[0].remote_id == "instance:other.social:1"


@pytest.mark.parametrize("address", ["http://other.social", "https://user:pass@other.social", "other.social/path", "other.social?token=x"])
def test_instance_rejects_invalid_addresses(address):
    with pytest.raises(AdapterError, match="HTTPS instance"):
        MastodonAdapter(client=Mock()).fetch_timeline("instance", address)


def test_direct_send_resolves_recipient_and_preserves_privacy():
    client = Mock()
    client.account_lookup.return_value = {"acct": "ada@remote.social"}
    client.status_post.return_value = status("3", "direct")
    result = MastodonAdapter(client=client).send_direct_message("@ada@remote.social", "Hello")
    assert result.remote_id == "3"
    client.status_post.assert_called_once_with(
        "@ada@remote.social Hello", visibility="direct", spoiler_text=None,
        language=None, in_reply_to_id=None,
    )
    client.status_post.side_effect = RuntimeError("forbidden")
    with pytest.raises(AdapterError):
        MastodonAdapter(client=client).send_direct_message("ada", "Hello")
    assert client.status_post.call_count == 2


@pytest.mark.parametrize("recipient,text", [("", "Hello"), ("ada bob", "Hello"), ("ada", " ")])
def test_direct_send_rejects_invalid_input(recipient, text):
    client = Mock()
    with pytest.raises(AdapterError):
        MastodonAdapter(client=client).send_direct_message(recipient, text)
    assert not client.mock_calls


def test_quote_post_is_read_with_quoted_author_content_warning_and_media():
    outer = status("outer")
    outer["content"] = (
        '<p class="quote-inline">RE: '
        '<a href="https://example.social/@ada/quoted">post URL</a></p>'
        "<p>My comment</p>")
    outer["quote"] = {
        "state": "accepted",
        "quoted_status": {
            "id": "quoted",
            "content": "<p>Original <strong>post</strong></p>",
            "spoiler_text": "Spoiler",
            "account": {"acct": "ada@example.social", "display_name": "Ada"},
            "media_attachments": [{"type": "image", "description": "A garden"}],
        },
    }
    item = _status_to_item(outer, account_id="mine")
    assert item.quote_of == "quoted" and item.is_quote
    assert item.text == (
        "My comment\n\nQuoted post by Ada:\nContent warning: Spoiler\n"
        "Original post\nMedia (image): A garden")
    assert "RE:" not in item.text
    assert "post URL" not in item.text


def test_link_quote_without_structured_quote_keeps_re_url():
    outer = status("outer")
    outer["content"] = (
        '<p>RE: <a href="https://old.example/@ada/42">'
        "https://old.example/@ada/42</a></p><p>My comment</p>")
    item = _status_to_item(outer)
    assert item.text == "RE: https://old.example/@ada/42\n\nMy comment"


def test_unavailable_quote_has_readable_state():
    outer = status("outer")
    outer["quote"] = {"state": "pending", "quoted_status_id": "quoted"}
    item = _status_to_item(outer)
    assert item.quote_of == "quoted"
    assert item.text.endswith("Quoted post pending approval.")


def test_blocked_quote_does_not_expose_hidden_content():
    outer = status("outer")
    outer["quote"] = {
        "state": "blocked_account",
        "quoted_status": {
            "id": "hidden", "content": "Secret text",
            "account": {"display_name": "Blocked user"},
        },
    }
    item = _status_to_item(outer)
    assert item.quote_of == "hidden"
    assert "Secret text" not in item.text
    assert item.text.endswith("Quoted post hidden because you blocked its author.")


def test_native_quote_publish_uses_mastodon_quote_parameter():
    client = Mock()
    client.status_post.return_value = status("created")
    MastodonAdapter(client=client).publish(PublishRequest(text="Comment", quote_of="42"))
    assert client.status_post.call_args.kwargs["quoted_status_id"] == "42"


def test_quote_capability_uses_instance_api_version():
    client = Mock(spec=["instance_v2"])
    client.instance_v2.return_value = {
        "version": "4.5.2", "api_versions": {"mastodon": 7}}
    caps = MastodonAdapter(client=client).probe_capabilities()
    assert caps.supports_quote
    assert caps.server_version == "4.5.2"
