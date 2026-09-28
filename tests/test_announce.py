"""Speech delivery, verbosity, and pending callbacks during shutdown."""

from unittest.mock import Mock

import pytest

from quill_social.ui.announce import Announcer


@pytest.fixture
def output(monkeypatch):
    import wx

    callbacks = []
    monkeypatch.setattr(wx, "CallAfter", lambda fn, *args: callbacks.append((fn, args)))
    speaker = Mock()
    monkeypatch.setattr(Announcer, "_make_speaker", lambda self: speaker)
    frame = Mock()
    frame.IsBeingDeleted.return_value = False
    announcer = Announcer(frame, verbosity="minimal")
    return announcer, frame, speaker, callbacks


@pytest.mark.parametrize("level", ["action", "automatic", "error"])
def test_explicit_feedback_and_opted_in_reading_bypass_minimal(output, level):
    announcer, frame, speaker, callbacks = output
    announcer.say("Favorited", level=level)
    speaker.speak.assert_not_called()
    fn, args = callbacks.pop()
    fn(*args)
    speaker.speak.assert_called_once_with("Favorited", interrupt=False)
    frame.SetStatusText.assert_called_once_with("Favorited")


def test_muted_routine_message_still_has_text_equivalent(output):
    announcer, frame, speaker, callbacks = output
    announcer.say("Background detail", level="verbose")
    fn, args = callbacks.pop()
    fn(*args)
    speaker.speak.assert_not_called()
    frame.SetStatusText.assert_called_once_with("Background detail")


def test_automatic_batch_preserves_order_without_interrupting(output):
    announcer, _, speaker, callbacks = output
    text = "Alice: First post.\nBob: Second post."
    announcer.say(text, level="automatic")
    assert len(callbacks) == 1
    fn, args = callbacks.pop()
    fn(*args)
    speaker.speak.assert_called_once_with(text, interrupt=False)


def test_error_explicitly_interrupts(output):
    announcer, _, speaker, callbacks = output
    announcer.error("Repost failed")
    fn, args = callbacks.pop()
    fn(*args)
    speaker.speak.assert_called_once_with("Repost failed", interrupt=True)


@pytest.mark.parametrize("destroyed", [False, True])
def test_pending_callback_does_not_speak_after_frame_closes(output, destroyed):
    announcer, frame, speaker, callbacks = output
    announcer.say("Favorited", level="action")
    if destroyed:
        frame.IsBeingDeleted.side_effect = RuntimeError("C++ object deleted")
    else:
        frame.IsBeingDeleted.return_value = True
    fn, args = callbacks.pop()
    fn(*args)
    speaker.speak.assert_not_called()
    frame.SetStatusText.assert_not_called()


def test_speech_failure_preserves_status_text(output):
    announcer, frame, speaker, callbacks = output
    speaker.speak.side_effect = OSError("screen reader exited")
    announcer.say("Reposted", level="action")
    fn, args = callbacks.pop()
    fn(*args)
    frame.SetStatusText.assert_called_once_with("Reposted")


def test_repeat_last_ignores_suppressed_messages(output):
    announcer, _, speaker, callbacks = output
    announcer.say("Thread sent.", "action")
    announcer.say("Routine update", "normal")
    announcer.repeat_last()
    fn, args = callbacks[-1]
    fn(*args)
    speaker.speak.assert_called_once_with("Thread sent.", interrupt=True)


def test_repeat_before_first_announcement(output):
    announcer, _, speaker, callbacks = output
    announcer.repeat_last()
    fn, args = callbacks[-1]
    fn(*args)
    speaker.speak.assert_called_once_with("No announcement to repeat.", interrupt=True)
