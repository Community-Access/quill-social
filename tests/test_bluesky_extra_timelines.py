from types import SimpleNamespace as NS
from unittest.mock import Mock

import pytest

from quill_social.adapters.base import AdapterError, PublishRequest
from quill_social.adapters.bluesky import BlueskyAdapter


def adapter():
    client = Mock()
    return BlueskyAdapter(did="did:me", account_id="account", client=client), client


def test_user_hashtag_and_list_feeds_use_distinct_endpoints():
    api, client = adapter()
    feed = client.app.bsky.feed
    post = {"uri": "at://post", "author": {}, "record": {"text": "Hello"}}
    feed.get_author_feed.return_value = {"feed": [{"post": post}]}
    feed.get_list_feed.return_value = {"feed": [{"post": post}]}
    feed.search_posts.return_value = {"posts": [post]}
    assert api.fetch_timeline("user", "@example.test")[0].text == "Hello"
    assert api.fetch_timeline("hashtag", "#hello")[0].account_id == "account"
    assert api.fetch_timeline("list", "at://list")[0].remote_id == "at://post"
    feed.get_author_feed.assert_called_once_with({"actor": "example.test", "limit": 40})
    assert feed.search_posts.call_args.args[0]["tag"] == ["hello"]
    with pytest.raises(AdapterError, match="instance"):
        api.fetch_timeline("local")


def test_lists_paginate_and_exclude_moderation_lists():
    api, client = adapter()
    client.app.bsky.graph.get_lists.side_effect = [
        {"lists": [{"uri": "one", "name": "Friends", "purpose": "app.bsky.graph.defs#curatelist"}], "cursor": "next"},
        {"lists": [{"uri": "two", "purpose": "app.bsky.graph.defs#modlist"}]},
    ]
    assert api.timeline_lists() == [("one", "Friends")]


def test_search_returns_posts_users_and_hashtag_result():
    api, client = adapter()
    post = {"uri": "at://post", "author": {}, "record": {"text": "Hello"}}
    client.app.bsky.feed.search_posts.return_value = {"posts": [post]}
    client.app.bsky.actor.search_actors.return_value = {"actors": [{
        "did": "did:plc:ada", "handle": "ada.test", "display_name": "Ada",
        "description": "Profile",
    }]}
    rows = api.search("#accessibility", limit=12)
    assert [row.remote_id for row in rows] == [
        "at://post", "search:user:did:plc:ada", "search:hashtag:accessibility"]
    assert rows[1].author_handle == "@ada.test"
    client.app.bsky.feed.search_posts.assert_called_once_with({"q": "#accessibility", "limit": 12})
    client.app.bsky.actor.search_actors.assert_called_once_with({"q": "#accessibility", "limit": 12})


def test_hashtag_search_does_not_call_post_or_user_search():
    api, client = adapter()
    rows = api.search("#quill", "hashtags")
    assert [row.text for row in rows] == ["Hashtag: #quill"]
    assert not client.app.bsky.feed.search_posts.called
    assert not client.app.bsky.actor.search_actors.called


def test_messages_use_chat_proxy_keep_conversation_and_skip_deleted():
    api, client = adapter()
    chat = client.with_bsky_chat_proxy.return_value.chat.bsky.convo
    chat.list_convos.return_value = {"convos": [{"id": "c1", "members": [{"did": "did:friend", "handle": "friend.test"}]}]}
    chat.get_messages.return_value = {"messages": [
        {"id": "m1", "text": "Private", "sender": {"did": "did:friend"}, "sent_at": "2026-09-21T10:00:00Z"},
        {"id": "deleted"},
    ]}
    items = api.fetch_timeline("messages")
    assert len(items) == 1
    assert items[0].remote_id == "chat:c1:m1"
    assert items[0].visibility == "direct"
    assert items[0].author_handle == "@friend.test"
    assert items[0].thread_root == "chat:c1"
    client.with_bsky_chat_proxy.assert_called_once()


@pytest.mark.parametrize("recipient", ["friend.test", "convo:c1"])
def test_direct_send_never_calls_public_publisher(recipient):
    api, client = adapter()
    chat = client.with_bsky_chat_proxy.return_value.chat.bsky.convo
    client.resolve_handle.return_value = NS(did="did:friend")
    chat.get_convo_for_members.return_value = {"convo": {"id": "c1"}}
    chat.send_message.return_value = {"id": "m2", "text": "Secret", "sender": {"did": "did:me"}}
    result = api.send_direct_message(recipient, "Secret")
    assert result.remote_id == "chat:c1:m2"
    assert result.item.visibility == "direct"
    chat.send_message.assert_called_once_with({"convo_id": "c1", "message": {"text": "Secret"}})
    client.send_post.assert_not_called()
    if recipient.startswith("convo:"):
        chat.get_convo.assert_called_once_with({"convo_id": "c1"})
    else:
        chat.get_convo_for_members.assert_called_once_with({"members": ["did:friend"]})


def test_private_content_cannot_escape_via_social_publish_or_repost():
    api, client = adapter()
    for request in [PublishRequest(text="secret", visibility="direct"),
                    PublishRequest(text="secret", in_reply_to="chat:c1:m1")]:
        with pytest.raises(AdapterError):
            api.publish(request)
    with pytest.raises(AdapterError):
        api.set_reblog("chat:c1:m1")
    client.send_post.assert_not_called()
