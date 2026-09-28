from datetime import UTC, datetime
from zoneinfo import ZoneInfo

from quill_social import a11y, time_display
from quill_social.fields import field_value
from quill_social.model import PublicationPlan, SocialItem
from quill_social.services import calendar


def ms(value):
    return int(datetime.fromisoformat(value).timestamp() * 1000)


def london_system(monkeypatch):
    class LocalDateTime(datetime):
        def astimezone(self, tz=None):
            return super().astimezone(ZoneInfo("Europe/London") if tz is None else tz)
    monkeypatch.setattr(time_display, "datetime", LocalDateTime)


def test_system_default_uses_each_dates_daylight_saving_rules(monkeypatch):
    london_system(monkeypatch)
    summer = ms("2026-07-01T23:30:00+00:00")
    winter = ms("2026-01-01T23:30:00+00:00")
    assert time_display.format_timestamp(summer) == "2026-07-02 00:30 BST"
    assert time_display.format_timestamp(winter) == "2026-01-01 23:30 GMT"
    assert time_display.format_timestamp(summer, "utc") == "2026-07-01 23:30 UTC"
    assert time_display.display_datetime(summer).timestamp() == summer / 1000


def test_utc_is_independent_of_system_timezone():
    assert time_display.format_timestamp(0, "utc") == "1970-01-01 00:00 UTC"
    assert time_display.format_timestamp(None) == "unknown"
    value = ms("2026-07-01T23:30:00+00:00")
    assert time_display.display_datetime(value, "utc").tzinfo is UTC


def test_timezone_preference_defaults_sanitizes_and_persists(tmp_path):
    assert a11y.load(tmp_path).display_timezone == "system"
    assert a11y.A11ySettings.from_dict({"verbosity": "minimal"}).display_timezone == "system"
    assert a11y.A11ySettings.from_dict({"display_timezone": "invalid"}).display_timezone == "system"
    settings = a11y.A11ySettings(display_timezone="utc")
    a11y.save(tmp_path, settings)
    assert a11y.load(tmp_path).display_timezone == "utc"


def test_old_post_dates_and_calendar_grouping_use_display_timezone(monkeypatch):
    london_system(monkeypatch)
    timestamp = ms("2026-07-01T23:30:00+00:00")
    item = SocialItem(created_at=timestamp)
    now = timestamp + 10 * 86400 * 1000
    assert field_value(item, "date", now=now) == "2026-07-02"
    assert field_value(item, "date", now=now, settings=a11y.A11ySettings(display_timezone="utc")) == "2026-07-01"
    plan = PublicationPlan(scheduled_for=timestamp)
    assert list(calendar.group_by_day([plan], tz="system")) == ["2026-07-02"]
    assert list(calendar.group_by_day([plan], tz="UTC")) == ["2026-07-01"]
    assert plan.scheduled_for == timestamp
