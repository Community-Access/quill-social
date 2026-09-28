"""Accessibility settings for QUILL Social (PRD 28, 6.5).

A small, persisted settings model covering the user-facing accessibility
levers: announcement verbosity, high-contrast colors, text scaling, reduced
motion, and a couple of speech toggles specific to social reading (whether to
speak the network prefix and engagement counts on every row, PRD 28.5). The
model is wx-free so it can be unit-tested and loaded before the UI is built;
the shell applies it to controls.

Settings live in ``a11y.json`` next to the database. They never affect data,
only presentation, so a bad value can be reset without risk. Mirrors
``quill_beacon.a11y``.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Literal

Verbosity = Literal["minimal", "normal", "verbose"]
SETTINGS_NAME = "a11y.json"

# Discrete scale steps keep font sizes predictable for screen-reader users who
# set them by keyboard, rather than a free-form slider.
SCALE_STEPS = (0.85, 0.95, 1.0, 1.1, 1.25, 1.5)
DEFAULT_SCALE_INDEX = 2  # 1.0


@dataclass
class A11ySettings:
    verbosity: Verbosity = "normal"
    high_contrast: bool = False
    scale_index: int = DEFAULT_SCALE_INDEX
    reduced_motion: bool = False
    # Social-specific speech toggles (PRD 28.5). Off-by-default keeps rows terse.
    speak_network_prefix: bool = True
    speak_engagement: bool = False
    announce_read_state: bool = True
    announce_timeline_summary: bool = False
    display_timezone: Literal["system", "utc"] = "system"
    ui_mode: Literal["standard", "advanced"] = "standard"
    reverse_timelines: bool = False
    condense_mentions: bool = False
    exclude_web_addresses: bool = False
    post_timestamps_relative: bool = True
    post_timestamps_12_hour: bool = False
    composition_word_wrap: bool = False
    separate_reply_recipients: bool = False
    ctrl_enter_to_send: bool = True
    provide_confirmations: bool = True
    open_single_link_without_dialog: bool = True
    remove_unicode: bool = False
    notification_first_sentence: bool = False
    focus_posts_on_startup: bool = False
    minimize_to_tray: bool = False
    timeline_limit: int = 60
    timeline_display_limit: int = 500
    navigation: dict = field(default_factory=dict)
    account_options: dict = field(default_factory=dict)

    @property
    def text_scale(self) -> float:
        if 0 <= self.scale_index < len(SCALE_STEPS):
            return SCALE_STEPS[self.scale_index]
        return 1.0

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> A11ySettings:
        from quill_social.navigation import sanitize_navigation
        from quill_social.reading_options import sanitize_account_options
        # Clamp/sanitize so a hand-edited file cannot break the UI.
        v = d.get("verbosity", "normal")
        if v not in ("minimal", "normal", "verbose"):
            v = "normal"
        idx = d.get("scale_index", DEFAULT_SCALE_INDEX)
        if not isinstance(idx, int) or not (0 <= idx < len(SCALE_STEPS)):
            idx = DEFAULT_SCALE_INDEX
        return cls(
            ui_mode=d.get("ui_mode") if d.get("ui_mode") in ("standard", "advanced") else "standard",
            display_timezone=d.get("display_timezone") if d.get("display_timezone") in ("system", "utc") else "system",
            verbosity=v,
            high_contrast=bool(d.get("high_contrast", False)),
            scale_index=idx,
            reduced_motion=bool(d.get("reduced_motion", False)),
            speak_network_prefix=bool(d.get("speak_network_prefix", True)),
            speak_engagement=bool(d.get("speak_engagement", False)),
            announce_read_state=bool(d.get("announce_read_state", True)),
            announce_timeline_summary=d.get("announce_timeline_summary") is True,
            reverse_timelines=d.get("reverse_timelines") is True,
            condense_mentions=d.get("condense_mentions") is True,
            exclude_web_addresses=d.get("exclude_web_addresses") is True,
            post_timestamps_relative=d.get("post_timestamps_relative", True) is not False,
            post_timestamps_12_hour=d.get("post_timestamps_12_hour") is True,
            composition_word_wrap=d.get("composition_word_wrap") is True,
            separate_reply_recipients=d.get("separate_reply_recipients") is True,
            ctrl_enter_to_send=d.get("ctrl_enter_to_send", True) is not False,
            provide_confirmations=d.get("provide_confirmations", True) is not False,
            open_single_link_without_dialog=d.get("open_single_link_without_dialog", True) is not False,
            remove_unicode=d.get("remove_unicode") is True,
            notification_first_sentence=d.get("notification_first_sentence") is True,
            focus_posts_on_startup=d.get("focus_posts_on_startup") is True,
            minimize_to_tray=d.get("minimize_to_tray") is True,
            timeline_limit=_bounded_int(d.get("timeline_limit"), 60, 1, 1000),
            timeline_display_limit=_bounded_int(d.get("timeline_display_limit"), 500, 1, 10000),
            navigation=sanitize_navigation(d.get("navigation", {})),
            account_options=sanitize_account_options(d.get("account_options", {})),
        )


def _bounded_int(value, default, low, high):
    return value if type(value) is int and low <= value <= high else default


def settings_path(data_dir: str | Path) -> Path:
    return Path(data_dir) / SETTINGS_NAME


def load(data_dir: str | Path) -> A11ySettings:
    p = settings_path(data_dir)
    if not p.exists():
        return A11ySettings()
    try:
        return A11ySettings.from_dict(json.loads(p.read_text(encoding="utf-8")))
    except Exception:
        return A11ySettings()


def save(data_dir: str | Path, settings: A11ySettings) -> None:
    settings_path(data_dir).write_text(
        json.dumps(settings.to_dict(), indent=2), encoding="utf-8")


def apply_to_frame(frame, settings: A11ySettings) -> None:
    """Apply high-contrast colors and text scaling to every control in a frame.

    Imported lazily (wx) by the shell; kept here so all a11y logic is in one
    place. Reduced motion is recorded on the frame for callers that animate.
    Walks the whole child tree so new panes pick up the theme automatically.
    """
    import wx

    frame._a11y = settings
    scale = settings.text_scale
    base = wx.Font(wx.NORMAL_FONT)
    font = base.Scaled(scale) if hasattr(base, "Scaled") else base

    fg, bg = ("black", "white")
    if settings.high_contrast:
        fg, bg = ("white", "black")

    def _style(ctrl) -> None:
        if ctrl is None:
            return
        try:
            ctrl.SetForegroundColour(wx.Colour(fg))
            ctrl.SetBackgroundColour(wx.Colour(bg))
        except Exception:
            pass
        try:
            ctrl.SetFont(font)
        except Exception:
            pass

    def _walk(win) -> None:
        _style(win)
        for child in win.GetChildren():
            _walk(child)

    try:
        _walk(frame)
        frame.Refresh()
    except Exception:
        pass
