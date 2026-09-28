"""Recovered-client incoming speech wording and presentation preferences."""

from types import SimpleNamespace

import pytest

from quill_social.a11y import A11ySettings
from quill_social.model import SocialItem
from quill_social.reading_options import automatic_text


def post(**values):
    return SocialItem(account_id="a", author_display="Alice", **values)


def event(kind, **values):
    return SimpleNamespace(kind=kind, actor_name="Bob", actor_handle="@bob", account_id="a", **values)


def test_home_speech_retains_whole_post_and_describes_account_context():
    item = post(text="First sentence. Second sentence.")
    settings = A11ySettings(notification_first_sentence=True)
    assert automatic_text(item, settings) == "New home post from Alice: First sentence. Second sentence."
    assert automatic_text(item, settings, account_label="Personal", account_count=2) == (
        "Personal. New home post from Alice: First sentence. Second sentence.")
    assert automatic_text(item, settings, account_label="Personal", account_count=2,
                          active_account_context=True) == "from Alice: First sentence. Second sentence."
    assert automatic_text(item, settings, scope="attention:mentions").startswith("New mention from Alice:")


@pytest.mark.parametrize("kind, expected", [
    ("favourite", "New notification from Bob favourited Alice: Hello."),
    ("reblog", "New notification from Bob boosted Alice: Hello."),
    ("mention", "New notification from Bob: Hello."),
    ("poll", "New notification. A poll from Bob has ended: Hello."),
    ("quote", "New notification from Bob quoted Alice: Hello."),
    ("quoted_update", "New notification from Bob: edited a post you quoted: Hello."),
])
def test_notification_actor_and_action_are_distinct_from_target_post(kind, expected):
    item = post(text="Hello. More detail.")
    settings = A11ySettings(notification_first_sentence=True)
    assert automatic_text(item, settings, scope="attention:notifications", notification=event(kind)) == expected
    assert item.text == "Hello. More detail."


def test_follow_without_a_post_and_notification_prefix():
    assert automatic_text(None, A11ySettings(), scope="attention:notifications",
                          notification=event("follow"), account_count=2, account_label="Work") == (
        "Work. New notification from Bob: followed you")
    assert automatic_text(None, A11ySettings(), scope="attention:notifications",
                          notification=event("follow_request"), active_account_context=True) == (
        "New notification from Bob: requested to follow you")


@pytest.mark.parametrize("mode, expected", [
    ("warning_only", "Content warning: Sensitive"),
    ("warning_then_text", "Content warning: Sensitive. Hello."),
    ("text_only", "Hello."),
])
def test_content_warning_policy_applies_to_incoming_speech(mode, expected):
    settings = A11ySettings(account_options={"a": {"content_warning_mode": mode}})
    item = post(text="Hello.", content_warning="Sensitive")
    assert automatic_text(item, settings) == f"New home post from Alice: {expected}"
    assert item.content_warning == "Sensitive"


def test_condensed_mentions_urls_unicode_and_boost_context():
    item = post(text="@one @two @three Café https://example.test www.example.test still open.",
                reblog_by="@booster")
    settings = A11ySettings(condense_mentions=True, exclude_web_addresses=True, remove_unicode=True)
    assert automatic_text(item, settings) == (
        "New home post from @booster boosted Alice: @one and 2 more. Cafe still open.")
    assert "https://example.test" in item.text
    assert "Café" in item.text


def test_empty_post_has_no_trailing_colon():
    assert automatic_text(post(), A11ySettings(), active_account_context=True) == "from Alice"


def test_notification_sentence_limit_does_not_cut_action_or_account_label():
    item = post(text="Hello. More.")
    result = automatic_text(item, A11ySettings(notification_first_sentence=True),
                            scope="attention:notifications", notification=event("favourite"),
                            account_count=2, account_label="Work. Account")
    assert result == "Work. Account. New notification from Bob favourited Alice: Hello."


def test_notification_first_sentence_includes_warning_before_post_body():
    item = post(text="Post body. More detail.", content_warning="Sensitive")
    settings = A11ySettings(notification_first_sentence=True)
    assert automatic_text(item, settings, scope="attention:notifications",
                          notification=event("mention")) == (
        "New notification from Bob: Content warning: Sensitive.")
    assert automatic_text(item, settings) == (
        "New home post from Alice: Content warning: Sensitive. Post body. More detail.")
