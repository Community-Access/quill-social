from quill_social.model import Account, Media, Poll, PollOption, SocialItem
from quill_social.services.timeline_export import timeline_text


def test_export_preserves_full_text_media_poll_and_timezone():
    account = Account(account_id="a", network="mastodon", handle="reader", instance="example.org")
    item = SocialItem(account_id="a", author_display="A writer", author_handle="@writer",
                      text="First line\nSecond line https://example.org", created_at=0,
                      content_warning="Spoilers", uri="https://example.org/post/1",
                      media=[Media(uri="https://example.org/image", alt_text="A bird")],
                      poll=Poll(options=[PollOption("Yes", 2)]))
    result = timeline_text("Home", [item], accounts={"a": account}, timezone="utc")
    for expected in (item.text, "Spoilers", "Alt text: A bird", "Yes: 2 votes",
                     "1970-01-01 00:00 UTC", "@reader@example.org", item.uri):
        assert expected in result
    assert not item.read


def test_empty_timeline_is_explicit():
    assert "No items." in timeline_text("Bookmarks", [], accounts={})
