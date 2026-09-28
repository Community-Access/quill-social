"""Readable, local timeline exports without changing navigation or read state."""
from collections.abc import Iterable, Mapping

from quill_social.model import Account, SocialItem
from quill_social.time_display import format_timestamp


def timeline_text(label: str, items: Iterable[SocialItem], *,
                  accounts: Mapping[str, Account], timezone: str = "system") -> str:
    lines = [label, "=" * len(label)]
    for number, item in enumerate(items, 1):
        author = item.author_display or item.author_handle or "Unknown author"
        lines.extend(["", f"{number}. {author} ({item.author_handle})",
                      f"Date: {format_timestamp(item.created_at, timezone)}"])
        account = accounts.get(item.account_id)
        lines.append(f"Account: {account.full_handle if account else item.account_id}")
        lines.append(f"Visibility: {item.visibility}")
        if item.reblog_by:
            lines.append(f"Boosted by: {item.reblog_by}")
        if item.content_warning:
            lines.append(f"Content warning: {item.content_warning}")
        lines.append(item.text)
        if item.uri:
            lines.append(f"Link: {item.uri}")
        for media in item.media:
            lines.append(f"Media ({media.kind}): {media.uri}")
            lines.append(f"Alt text: {media.alt_text or '(not provided)'}")
            if media.transcript:
                lines.append(f"Transcript: {media.transcript}")
        if item.poll:
            lines.append("Poll:")
            lines.extend(f"- {option.title}: {option.votes} votes" for option in item.poll.options)
    if len(lines) == 2:
        lines.append("No items.")
    return "\n".join(lines) + "\n"
