"""Screen-reader announcements (PRD 6.3, 28.3, 28.5).

Important state is never hidden: every announcement reaches the user as text.
The announcer speaks through the most reliable channel available, in order:

1. ``accessible_output2`` -- direct output to a supported running screen reader,
   with system speech as a fallback, independent of keyboard focus.
2. The frame's status bar, which is always a text equivalent and is picked up
   by screen readers on review.

Verbosity gates chatter: ``minimal`` speaks errors and action confirmations,
``normal`` speaks routine confirmations, ``verbose`` adds background detail.
Opted-in automatic post reading also speaks at every verbosity level; callers
apply the user's account and timeline announcement preferences first.
Announcements are always marshaled onto the UI thread with ``wx.CallAfter`` so
background workers can announce safely (PRD 29.3).
"""

from __future__ import annotations

_LEVELS = {"minimal": 0, "normal": 1, "verbose": 2}


class Announcer:
    def __init__(self, frame, *, verbosity: str = "normal") -> None:
        self.frame = frame
        self.verbosity = verbosity
        self.last_message = ""
        self._speaker = self._make_speaker()

    def _make_speaker(self):
        try:
            import accessible_output2.outputs.auto  # type: ignore

            return accessible_output2.outputs.auto.Auto()
        except Exception:
            return None

    def set_verbosity(self, verbosity: str) -> None:
        if verbosity in _LEVELS:
            self.verbosity = verbosity

    def _allowed(self, level: str) -> bool:
        if level in {"error", "action", "automatic"}:
            return True
        return _LEVELS.get(level, 1) <= _LEVELS.get(self.verbosity, 1)

    def say(self, text: str, level: str = "normal", *, interrupt: bool = False) -> None:
        """Queue speech and its status text equivalent on the UI thread.

        Send a batch of automatic posts as one message to preserve their order.
        Speech queues by default; only explicit interruptions cancel speech.
        """
        if not text:
            return
        if self._allowed(level):
            self.last_message = text
        import wx

        wx.CallAfter(self._emit, text, interrupt, self._allowed(level))

    def _emit(self, text: str, interrupt: bool, speak: bool = True) -> None:
        # Queued worker callbacks can outlive the frame during shutdown.
        try:
            if not self.frame or self.frame.IsBeingDeleted():
                return
        except RuntimeError:
            return
        if speak and self._speaker is not None:
            try:
                self._speaker.speak(text, interrupt=interrupt)
            except Exception:
                pass
        try:
            if self.frame and self.frame.GetStatusBar():
                self.frame.SetStatusText(text)
        except Exception:
            pass

    def error(self, text: str) -> None:
        self.say(text, "error", interrupt=True)

    def repeat_last(self) -> None:
        self.say(self.last_message or "No announcement to repeat.", "action", interrupt=True)
