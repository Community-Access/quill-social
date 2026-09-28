from types import SimpleNamespace

import pytest

from quill_social.adapters.base import AdapterError, PublishRequest
from quill_social.adapters.bluesky import BlueskyAdapter
from quill_social.adapters.mastodon import MastodonAdapter
from quill_social.model import Draft, Media, Poll, PollOption, PublicationPlan, now_ms
from quill_social.services.scheduler import process_plan
from tests.conftest import ScriptedAdapter


def test_mastodon_upload_preserves_alt_text_and_delivery_options():
    calls = []
    client = SimpleNamespace(
        media_post=lambda path, **kw: calls.append((path, kw)) or {"id": "media1"},
        status_post=lambda text, **kw: calls.append((text, kw)) or {"id": "post1"})
    MastodonAdapter(client=client).publish(PublishRequest(
        text="photo", visibility="followers", media=[Media(local_path="photo.jpg", alt_text="A bird")],
        idempotency_key="draft1"))
    assert calls[0] == ("photo.jpg", {"description": "A bird", "synchronous": True})
    assert calls[1][1]["media_ids"] == ["media1"]
    assert calls[1][1]["visibility"] == "private"
    assert calls[1][1]["idempotency_key"] == "draft1"


def test_mastodon_poll_is_delivered():
    calls = []
    client = SimpleNamespace(make_poll=lambda options, **kw: (options, kw),
        status_post=lambda text, **kw: calls.append(kw) or {"id": "poll1"})
    poll = Poll(options=[PollOption(title="Yes"), PollOption(title="No")],
                multiple=True, expires_at=now_ms() + 3_600_000)
    MastodonAdapter(client=client).publish(PublishRequest(text="Choose", poll=poll))
    options, settings = calls[0]["poll"]
    assert options == ["Yes", "No"]
    assert settings["multiple"]
    assert 3590 <= settings["expires_in"] <= 3600


def test_bluesky_preserves_reply_root_and_quote():
    pytest.importorskip("atproto")
    parent_uri = "at://did:plc:abc/app.bsky.feed.post/parent"
    root_uri = "at://did:plc:abc/app.bsky.feed.post/root"
    quoted_uri = "at://did:plc:abc/app.bsky.feed.post/quoted"
    calls = []
    def get_posts(uris):
        return SimpleNamespace(posts=[{"uri": uri, "cid": "bafycid",
            "record": {"reply": {"root": {"uri": root_uri, "cid": "bafyroot"}}}}
            for uri in uris])
    client = SimpleNamespace(get_posts=get_posts,
        send_post=lambda **kw: calls.append(kw) or SimpleNamespace(uri="new"))
    BlueskyAdapter(client=client).publish(PublishRequest(text="reply", in_reply_to=parent_uri, quote_of=quoted_uri))
    assert calls[0]["reply_to"].parent.uri == parent_uri
    assert calls[0]["reply_to"].root.uri == root_uri
    assert calls[0]["embed"].record.uri == quoted_uri


def test_bluesky_rejects_unsupported_content_before_posting():
    client = SimpleNamespace(send_post=lambda **kw: pytest.fail("Must not discard content"))
    for request in [PublishRequest(text="poll", poll=Poll()),
                    PublishRequest(text="video", media=[Media(kind="video", local_path="movie.mp4")])]:
        with pytest.raises(AdapterError) as exc:
            BlueskyAdapter(client=client).publish(request)
        assert exc.value.kind == "validation"


def test_scheduled_partial_thread_requires_review_without_retry():
    adapter = ScriptedAdapter(["ok", AdapterError("Disconnected")])
    draft = Draft(text="A word. " * 180, thread_mode=True, media=[Media(local_path="image.jpg")])
    plan = PublicationPlan(draft_id=draft.draft_id)
    step = process_plan(plan, adapter, draft)
    assert step.review_required and not step.retry_scheduled
    assert plan.state == "partial" and plan.remote_id == "r1"
    assert adapter.calls[0].media == draft.media
    assert not adapter.calls[1].media
    assert adapter.calls[1].in_reply_to == "r1"


@pytest.mark.parametrize("quoted", [False, True])
def test_bluesky_image_upload_preserves_alt_text_and_quote(tmp_path, quoted):
    pytest.importorskip("atproto")
    from atproto import models
    path = tmp_path / "image.png"
    path.write_bytes(b"image bytes")
    uploaded, sent = [], []
    blob = models.ComAtprotoRepoUploadBlob.Response.model_validate({"blob": {
        "ref": {"$link": "bafkreibm6jg3ux5qumhcn2ncldx43eivgvzktycxqqvgzd3a3tmedmjliq"},
        "mimeType": "image/png", "size": 11}})
    uri = "at://did:plc:abc/app.bsky.feed.post/quote"
    client = SimpleNamespace(
        upload_blob=lambda data: uploaded.append(data) or blob,
        get_posts=lambda uris: SimpleNamespace(posts=[{"uri": uri, "cid": "bafycid"}]),
        send_post=lambda **kw: sent.append(kw) or SimpleNamespace(uri="new"))
    BlueskyAdapter(client=client).publish(PublishRequest(text="photo", quote_of=uri if quoted else "",
        media=[Media(kind="image", local_path=str(path), alt_text="A bird")]))
    assert uploaded == [b"image bytes"]
    embed = sent[0]["embed"]
    if quoted:
        assert embed.record.record.uri == uri
        embed = embed.media
    assert embed.images[0].alt == "A bird"
    assert embed.images[0].image.mime_type == "image/png"


def test_scheduled_poll_duration_is_relative_to_delivery():
    created = 1000
    draft = Draft(created=created, poll=Poll(expires_at=created+300000))
    adapter = ScriptedAdapter()
    process_plan(PublicationPlan(), adapter, draft, now=9999999)
    assert adapter.calls[0].poll_expires_in == 300
