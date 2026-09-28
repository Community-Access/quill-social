"""Guarded headless tests for the composer dialog.

Skips cleanly when wx cannot initialize. Covers the pure schedule helper, a
headless construction with real accounts, draft building with media (including
alt text) and a native poll, and that the schedule action records an epoch-ms
schedule time. Never calls MainLoop; EndModal is stubbed so the finish path can
be exercised without a modal loop.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

wx = pytest.importorskip("wx")

from quill_social.capabilities import Capabilities  # noqa: E402
from quill_social.model import Account, Media, SocialItem  # noqa: E402
from quill_social.ui import composer as composer_mod  # noqa: E402
from quill_social.ui.composer import (  # noqa: E402
    ComposerDialog,
    media_kind_for_path,
    schedule_to_ms,
)


@pytest.fixture
def app():
    try:
        application = wx.App()
    except Exception:  # pragma: no cover - no display
        pytest.skip("wx cannot initialize in this environment")
    yield application
    application.Destroy()


def _accounts() -> list[Account]:
    return [Account(account_id="acct_1", network="mock", handle="me", is_default=True)]


def _caps(accounts) -> dict[str, Capabilities]:
    return {a.account_id: Capabilities(network=a.network) for a in accounts}


@pytest.mark.parametrize("separate", [False, True])
def test_reply_recipients_are_included_in_draft_and_character_count(app, separate):
    parent = wx.Frame(None)
    parent.a11y = SimpleNamespace(separate_reply_recipients=separate)
    accounts = _accounts()
    reply = SocialItem(account_id="acct_1", remote_id="post", author_handle="friend@example.org")
    dlg = ComposerDialog(parent, accounts, _caps(accounts), reply_to=reply)
    try:
        if separate:
            assert dlg.editor.GetValue() == ""
            assert dlg.reply_recipients.GetValue() == "@friend@example.org"
            dlg.reply_recipients.SetValue("friend@example.org, @second@example.org")
            dlg.editor.SetValue("Hello")
            expected = "@friend@example.org @second@example.org Hello"
        else:
            assert dlg.reply_recipients is None
            assert dlg.editor.GetValue() == "@friend@example.org "
            dlg.editor.SetValue(dlg.editor.GetValue() + "Hello")
            expected = "@friend@example.org Hello"
        draft = dlg._build_draft()
        assert draft.text == expected
        assert draft.in_reply_to == "post"
        dlg._refresh_report()
        assert f"{len(expected)}/" in dlg.report.GetValue()
    finally:
        dlg.Destroy()
        parent.Destroy()


@pytest.mark.parametrize("ctrl_send", [False, True])
@pytest.mark.parametrize("wrap", [False, True])
def test_composition_keyboard_and_wrap_preferences(app, monkeypatch, ctrl_send, wrap):
    parent = wx.Frame(None)
    parent.a11y = SimpleNamespace(ctrl_enter_to_send=ctrl_send, composition_word_wrap=wrap)
    accounts = _accounts()
    dlg = ComposerDialog(parent, accounts, _caps(accounts))
    try:
        assert bool(dlg.editor.GetWindowStyleFlag() & wx.TE_DONTWRAP) is not wrap
        actions = []
        monkeypatch.setattr(dlg, "_finish", actions.append)
        monkeypatch.setattr(wx.Window, "FindFocus", staticmethod(lambda: dlg.editor))
        for control, shift, alt in ((False, False, False), (True, False, False),
                                    (False, True, False), (True, True, False),
                                    (False, False, True)):
            event = wx.KeyEvent(wx.wxEVT_CHAR_HOOK)
            event.KeyCode = wx.WXK_RETURN
            event.SetControlDown(control)
            event.SetShiftDown(shift)
            event.SetAltDown(alt)
            before = len(actions)
            dlg._on_char_hook(event)
            sends = control == ctrl_send and not shift and not alt
            assert len(actions) == before + int(sends)
            assert event.GetSkipped() is not sends
        monkeypatch.setattr(wx.Window, "FindFocus", staticmethod(lambda: dlg.cw))
        event = wx.KeyEvent(wx.wxEVT_CHAR_HOOK)
        event.KeyCode = wx.WXK_RETURN
        event.SetControlDown(ctrl_send)
        before = len(actions)
        dlg._on_char_hook(event)
        assert len(actions) == before
        assert event.GetSkipped()
    finally:
        dlg.Destroy()
        parent.Destroy()


def test_composition_preferences_panel_saves_each_setting(app):
    from quill_social.ui.composition_preferences import CompositionPreferencesPanel
    parent = wx.Frame(None)
    settings = SimpleNamespace(composition_word_wrap=False, separate_reply_recipients=False,
                               ctrl_enter_to_send=True, provide_confirmations=True)
    panel = CompositionPreferencesPanel(parent, settings)
    try:
        for name in vars(settings):
            control = getattr(panel, name)
            control.SetValue(not getattr(settings, name))
        panel.apply(settings)
        assert vars(settings) == dict(composition_word_wrap=True, separate_reply_recipients=True,
                                     ctrl_enter_to_send=False, provide_confirmations=False)
    finally:
        parent.Destroy()


def test_selected_account_is_default_target_but_others_remain_available(app):
    accounts = _accounts() + [Account(account_id="second", network="mock", handle="second")]
    dialog = ComposerDialog(None, accounts, _caps(accounts), selected_account_id="second")
    try:
        assert dialog.accounts_box.GetCount() == 2
        assert not dialog.accounts_box.IsChecked(0)
        assert dialog.accounts_box.IsChecked(1)
        dialog.accounts_box.Check(0, True)
        assert dialog.accounts_box.IsChecked(0)
    finally:
        dialog.Destroy()


def test_schedule_to_ms_helper():
    # 2030-01-02 03:04 UTC -> known epoch ms.
    assert schedule_to_ms("2030-01-02", "03:04") == 1893553440000
    # Empty / malformed inputs return None.
    assert schedule_to_ms("", "03:04") is None
    assert schedule_to_ms("2030-01-02", "") is None
    assert schedule_to_ms("2030-13-02", "03:04") is None  # bad month
    assert schedule_to_ms("2030-01-02", "24:00") is None  # bad hour


def test_media_kind_for_path():
    assert media_kind_for_path("photo.PNG") == "image"
    assert media_kind_for_path("clip.mp4") == "video"
    assert media_kind_for_path("voice.m4a") == "audio"
    assert media_kind_for_path("data.bin") == "unknown"


def test_dialog_constructs_headlessly(app):
    accounts = _accounts()
    dlg = ComposerDialog(None, accounts, _caps(accounts))
    try:
        # Single account is auto-selected, report renders without error.
        assert dlg._selected_account_ids() == ["acct_1"]
        assert isinstance(dlg.report.GetValue(), str)
    finally:
        dlg.Destroy()


def test_build_draft_with_media_and_poll(app):
    accounts = _accounts()
    dlg = ComposerDialog(None, accounts, _caps(accounts))
    try:
        dlg.editor.SetValue("Hello there")
        dlg._media = [
            Media(kind="image", local_path="/tmp/a.png", alt_text="a cat"),
            Media(kind="image", local_path="/tmp/b.png"),
        ]
        dlg.poll_toggle.SetValue(True)
        dlg._poll_options = ["Yes", "No", ""]
        dlg.poll_multiple.SetValue(True)

        draft = dlg._build_draft()
        assert len(draft.media) == 2
        assert draft.media[0].alt_text == "a cat"
        assert draft.media[0].has_alt
        assert not draft.media[1].has_alt
        assert draft.poll is not None
        assert [o.title for o in draft.poll.options] == ["Yes", "No"]
        assert draft.poll.multiple is True
        assert draft.poll.expires_at is not None

        # Report reflects media and poll.
        dlg._refresh_report()
        report = dlg.report.GetValue()
        assert "attachment" in report
        assert "Poll" in report
    finally:
        dlg.Destroy()


def test_schedule_action_sets_schedule_at(app):
    accounts = _accounts()
    now_ms = 1_700_000_000_000
    dlg = ComposerDialog(None, accounts, _caps(accounts), now_ms=now_ms, ui_mode="advanced")
    try:
        dlg.editor.SetValue("Scheduled post")
        dlg.schedule_date.SetValue("2030-06-01")
        dlg.schedule_time.SetValue("09:30")
        dlg.EndModal = lambda code: None  # avoid needing a modal loop

        dlg._finish("schedule")
        assert dlg.result_action == "schedule"
        assert dlg.result_draft is not None
        assert dlg.result_schedule_at == schedule_to_ms("2030-06-01", "09:30")
    finally:
        dlg.Destroy()


def test_default_schedule_prefilled_one_hour_ahead(app):
    accounts = _accounts()
    now_ms = 1_700_000_000_000
    dlg = ComposerDialog(None, accounts, _caps(accounts), now_ms=now_ms, ui_mode="advanced")
    try:
        got = schedule_to_ms(dlg.schedule_date.GetValue(), dlg.schedule_time.GetValue())
        # Prefilled default is one hour ahead of the injected now (minute precision).
        assert got is not None
        assert abs(got - (now_ms + composer_mod._ONE_HOUR_MS)) < 60_000
    finally:
        dlg.Destroy()


def test_standard_composer_has_short_primary_tab_sequence(app):
    accounts = _accounts()
    dlg = ComposerDialog(None, accounts, _caps(accounts))
    try:
        dlg.Show()
        app.Yield()
        assert dlg.ui_mode == "standard"
        assert not dlg.options_panel.IsShown()
        assert not dlg.accounts_box.IsShownOnScreen()
        assert not dlg.schedule_btn.IsShown()
        assert not dlg.editor.HasFlag(wx.TE_PROCESS_TAB)
        dlg.editor.SetFocus()
        app.Yield()
        for expected in (dlg.visibility, dlg.publish_btn, dlg.save_btn):
            wx.Window.FindFocus().Navigate()
            app.Yield()
            assert wx.Window.FindFocus() == expected
    finally:
        dlg.Destroy()


def test_standard_options_keep_draft_values_when_collapsed(app):
    accounts = _accounts()
    dlg = ComposerDialog(None, accounts, _caps(accounts))
    try:
        dlg.Show()
        dlg.more_options.SetValue(True)
        dlg._on_more_options()
        assert dlg.accounts_box.IsShownOnScreen()
        for control in (dlg.cw, dlg.media_list, dlg.poll_toggle, dlg.thread_mode):
            assert control.IsShownOnScreen()
        for control in (dlg.template_choice, dlg.template_btn,
                        dlg.schedule_date, dlg.schedule_time, dlg.schedule_btn):
            assert not control.IsShownOnScreen()
        dlg.editor.SetValue("Hello")
        dlg.cw.SetValue("Spoilers")
        dlg._media = [Media(kind="image", local_path="image.png", alt_text="A bird")]
        dlg.poll_toggle.SetValue(True)
        dlg._poll_options = ["Yes", "No"]
        dlg.thread_mode.SetValue(True)
        expected = dlg._build_draft()
        dlg.cw.SetFocus()
        app.Yield()
        dlg.more_options.SetValue(False)
        dlg._on_more_options()
        app.Yield()
        assert wx.Window.FindFocus() == dlg.more_options
        # Draft IDs/timestamps are generated for each snapshot; compare the content.
        actual = dlg._build_draft()
        for field in ("text", "targets", "visibility", "content_warning", "thread_mode", "media", "poll"):
            assert getattr(actual, field) == getattr(expected, field)
        assert "Spoilers" in dlg.report.GetValue()
        assert "attachment" in dlg.report.GetValue()
        assert "Poll" in dlg.report.GetValue()
        assert actual.thread_mode
        assert "thread splitting" in dlg.more_options.GetLabel()
        assert "content warning" in dlg.more_options.GetLabel()
        assert "poll" in dlg.more_options.GetLabel()
    finally:
        dlg.Destroy()


def test_standard_schedule_action_remains_unavailable_with_options_expanded(app, monkeypatch):
    accounts = _accounts()
    dlg = ComposerDialog(None, accounts, _caps(accounts))
    messages = []
    monkeypatch.setattr(wx, "MessageBox", lambda message, *args: messages.append(message))
    try:
        dlg.editor.SetValue("Scheduled post")
        dlg.EndModal = lambda code: None
        for expanded in (False, True):
            dlg.more_options.SetValue(expanded)
            dlg._on_more_options()
            dlg._finish("schedule")
            assert dlg.result_action == ""
            assert dlg.result_schedule_at is None
            assert not dlg.schedule_btn.IsShown()
        assert len(messages) == 2
        assert all("Advanced mode" in message for message in messages)
    finally:
        dlg.Destroy()


def test_quote_dialog_has_context_and_initial_link_text(app):
    accounts = _accounts()
    dlg = ComposerDialog(
        None, accounts, _caps(accounts), quote_mode=True,
        initial_text="RE: https://example.social/post ")
    try:
        assert dlg.GetTitle() == "Quote"
        assert dlg.editor.GetValue() == "RE: https://example.social/post "
        assert dlg.editor.GetInsertionPoint() == len(dlg.editor.GetValue())
    finally:
        dlg.Destroy()


def test_standard_thread_draft_can_publish_and_be_saved(app, monkeypatch):
    accounts = _accounts()
    dlg = ComposerDialog(None, accounts, _caps(accounts))
    messages = []
    monkeypatch.setattr(wx, "MessageBox", lambda message, *args: messages.append(message))
    try:
        dlg.editor.SetValue("Existing thread draft")
        dlg.thread_mode.SetValue(True)
        dlg.EndModal = lambda code: None
        dlg._finish("publish")
        assert dlg.result_action == "publish"
        assert dlg.result_draft.thread_mode
        assert not messages
        dlg._finish("save")
        assert dlg.result_action == "save"
        assert dlg.result_draft.thread_mode
    finally:
        dlg.Destroy()


def test_standard_composer_publishes_an_ordinary_post(app):
    accounts = _accounts()
    dlg = ComposerDialog(None, accounts, _caps(accounts))
    try:
        dlg.editor.SetValue("An everyday post")
        dlg.EndModal = lambda code: None
        dlg._finish("publish")
        assert dlg.result_action == "publish"
        assert dlg.result_draft.text == "An everyday post"
        assert not dlg.result_draft.thread_mode
        assert dlg.result_schedule_at is None
    finally:
        dlg.Destroy()


def test_composer_inherits_advanced_mode_with_all_options_visible(app):
    from types import SimpleNamespace

    parent = wx.Frame(None)
    parent.a11y = SimpleNamespace(ui_mode="advanced")
    accounts = _accounts()
    dlg = ComposerDialog(parent, accounts, _caps(accounts))
    try:
        assert dlg.ui_mode == "advanced"
        assert dlg.more_options is None
        for control in (dlg.accounts_box, dlg.cw, dlg.media_list, dlg.poll_toggle,
                        dlg.thread_mode, dlg.template_choice, dlg.schedule_date,
                        dlg.schedule_time, dlg.schedule_btn):
            assert control.IsShown()
    finally:
        dlg.Destroy()
        parent.Destroy()
