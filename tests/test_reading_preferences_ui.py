"""Reading preferences apply immediately and keep the selected post."""
import pytest

wx = pytest.importorskip("wx")


def test_reading_preferences_apply_and_cancel(tmp_path, monkeypatch):
    from quill_social import a11y
    from quill_social.ui.app import PreferencesDialog, SocialFrame

    monkeypatch.setenv("QUILLSOCIAL_DATA", str(tmp_path))
    application = wx.App()
    frame = SocialFrame()
    try:
        original = [item.item_id for item in frame._items]
        selected = frame._current_item().item_id
        dialog = PreferencesDialog(frame, frame.a11y)
        assert [dialog.notebook.GetPageText(i) for i in range(2)] == ["General", "Reading"]
        for name in ("reverse_timelines", "condense_mentions", "exclude_web_addresses", "post_timestamps_12_hour"):
            dialog._reading_controls[name].SetValue(True)
        dialog._reading_controls["post_timestamps_relative"].SetValue(False)
        monkeypatch.setattr(dialog, "ShowModal", lambda: wx.ID_OK)
        dialog.run_and_apply(frame)
        assert [item.item_id for item in frame._items] == original[::-1]
        assert frame._current_item().item_id == selected
        assert a11y.load(frame.data_dir) == frame.a11y
        assert frame.a11y.condense_mentions
        assert not frame.a11y.post_timestamps_relative
        dialog = PreferencesDialog(frame, frame.a11y)
        dialog._reading_controls["reverse_timelines"].SetValue(False)
        monkeypatch.setattr(dialog, "ShowModal", lambda: wx.ID_CANCEL)
        dialog.run_and_apply(frame)
        assert frame.a11y.reverse_timelines
        assert [item.item_id for item in frame._items] == original[::-1]
    finally:
        frame._on_close(None)
        application.Destroy()
