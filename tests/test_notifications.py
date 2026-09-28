"""Tests for notifications and attention management (PRD 25)."""

from quill_social.adapters.base import NotificationEvent
from quill_social.model import SocialItem
from quill_social.services import notifications as notif


def _n(category="favourite", actor="ada", subject="post1", created=0, **kw):
    return notif.NotificationItem(
        category=category,
        actor_handle=f"@{actor}",
        actor_display=actor.title(),
        subject_id=subject,
        created=created,
        **kw,
    )


def test_in_quiet_hours_same_day():
    assert notif.in_quiet_hours(600, (540, 720))  # 10:00 within 09:00-12:00
    assert not notif.in_quiet_hours(800, (540, 720))


def test_in_quiet_hours_crosses_midnight():
    window = (1320, 420)  # 22:00 - 07:00
    assert notif.in_quiet_hours(1380, window)  # 23:00
    assert notif.in_quiet_hours(60, window)  # 01:00
    assert not notif.in_quiet_hours(600, window)  # 10:00


def test_only_from_drops_others():
    policy = notif.NotificationPolicy(only_from=["@grace"])
    dropped = notif.classify(policy, _n(actor="ada"), now=0)
    assert dropped.delivered is False
    kept = notif.classify(policy, _n(actor="grace"), now=0)
    assert kept.delivered is True


def test_quiet_hours_holds_for_digest():
    policy = notif.NotificationPolicy(suppress_during_quiet_hours=True)
    # now = 23:00 UTC in ms (23*60 minutes since epoch day start)
    now = 23 * 60 * 60_000
    decision = notif.classify(policy, _n(), now=now, quiet_hours=(1320, 420))
    assert decision.add_silently is True
    assert decision.digest is True
    assert decision.speak is False


def test_critical_category_ignores_quiet_hours():
    policy = notif.NotificationPolicy(suppress_during_quiet_hours=True)
    now = 23 * 60 * 60_000
    decision = notif.classify(
        policy, _n(category="delivery_failure"), now=now, quiet_hours=(1320, 420)
    )
    assert decision.speak is True


def test_focus_mutes_speech_and_sound():
    policy = notif.NotificationPolicy(speak=True, sound=True)
    focus = notif.FocusMode(name="meeting", mute_speech=True, mute_sounds=True)
    decision = notif.classify(policy, _n(), now=0, focus=focus)
    assert decision.speak is False
    assert decision.sound is False
    assert decision.add_silently is True


def test_group_duplicates_collapses():
    items = [
        _n(actor="ada", created=1),
        _n(actor="grace", created=2),
        _n(actor="alan", created=3),
    ]
    grouped = notif.group_duplicates(items)
    assert len(grouped) == 1
    assert grouped[0].count == 3
    assert grouped[0].actors == ["Ada", "Grace", "Alan"]


def test_group_duplicates_keeps_distinct_subjects():
    items = [_n(subject="p1"), _n(subject="p2")]
    assert len(notif.group_duplicates(items)) == 2


def test_build_digest_text():
    items = [
        _n(actor="ada", created=1),
        _n(actor="grace", created=2),
        _n(category="reply", actor="alan", subject="post9", created=3),
    ]
    text = notif.build_digest(items)
    assert "notification group(s)" in text
    assert "favourited your post" in text
    assert "replied to your post" in text


def test_poll_notification_says_the_poll_has_ended():
    item = _n(category="poll", actor="ada", text="Which option?")
    assert notif.summary(item) == "A poll from Ada has ended: Which option?"
    assert "A poll from Ada has ended" in notif.build_digest([item])


def test_policy_persistence(store):
    p = notif.NotificationPolicy(account_id="acct1", category="mention", speak=False)
    notif.save_policy(store, p)
    loaded = notif.get_policy(store, "acct1", "mention")
    assert loaded is not None
    assert loaded.speak is False
    assert len(notif.load_policies(store)) == 1


def test_event_persistence_keeps_reactions_and_statusless_follow_distinct(store):
    post = SocialItem(item_id="local-post", account_id="mine", network="mastodon",
                      remote_id="42", uri="https://example/@me/42", text="My post")
    events = [
        NotificationEvent("fav", "favourite", "Ada", post, "mine", "@ada", 3),
        NotificationEvent("boost", "reblog", "Ben", post, "mine", "@ben", 2),
        NotificationEvent("follow", "follow", "Cat", None, "mine", "@cat", 1),
    ]
    items = [notif.from_event(event, "mastodon") for event in events]
    notif.save_items(store, items)
    loaded = notif.list_items(store, "mine")
    assert [item.notif_id for item in loaded] == ["fav", "boost", "follow"]
    assert [item.category for item in loaded] == ["favourite", "repost", "follow"]
    rows = [notif.to_social_item(item) for item in loaded]
    assert rows[0].remote_id == rows[1].remote_id == "42"
    assert rows[0].item_id != rows[1].item_id
    assert rows[2].text == "Cat followed you"


def test_notification_read_state_survives_refresh(store):
    item = _n(account_id="mine", notif_id="one", created=10)
    notif.save_items(store, [item])
    row_id = notif.item_key(item)
    assert notif.mark_read(store, row_id)
    notif.save_items(store, [_n(account_id="mine", notif_id="one", created=10)])
    assert notif.list_items(store, "mine")[0].read
