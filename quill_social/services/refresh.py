"""Fetch account updates without touching the UI or its SQLite connection."""
from __future__ import annotations

from dataclasses import dataclass, field

from quill_social.adapters.base import AdapterError, NotificationEvent
from quill_social.adapters.registry import adapter_for
from quill_social.model import SocialItem
from quill_social.services.notifications import NotificationItem, from_event


@dataclass
class RefreshResult:
    items: list[SocialItem] = field(default_factory=list)
    posts: int = 0
    errors: list[str] = field(default_factory=list)
    home_positions: dict[str, str] = field(default_factory=dict)
    notification_keys: set[tuple[str, str]] = field(default_factory=set)
    home_keys: set[tuple[str, str]] = field(default_factory=set)
    events: list[NotificationEvent] = field(default_factory=list)
    notifications: list[NotificationItem] = field(default_factory=list)
    capabilities: dict[str, object] = field(default_factory=dict)
    successful_accounts: set[str] = field(default_factory=set)


def fetch_accounts(accounts, credentials, *, limit: int = 60, sync_accounts=()) -> RefreshResult:
    result = RefreshResult()
    for account in accounts:
        try:
            adapter = adapter_for(account, credentials)
            for item in adapter.home_timeline(limit=limit):
                item.account_id = account.account_id
                result.items.append(item)
                result.posts += 1
                result.home_keys.add((item.account_id, item.remote_id))
            event_getter = getattr(adapter, "notification_events", None)
            if event_getter:
                events = event_getter(limit=40)
            else:
                events = [NotificationEvent(item.remote_id, "mention", item.author_display,
                                             item=item) for item in adapter.notifications(limit=20)]
            for event in events:
                event.account_id = account.account_id
                result.events.append(event)
                if event.item is not None:
                    event.item.account_id = account.account_id
                    result.items.append(event.item)
                    result.notification_keys.add((account.account_id, event.item.remote_id))
                result.notifications.append(from_event(event, account.network))
            probe = getattr(adapter, "probe_capabilities", None)
            if probe:
                try:
                    result.capabilities[account.account_id] = probe()
                except Exception:
                    pass
            result.successful_accounts.add(account.account_id)
            if account.account_id in sync_accounts and account.network == "mastodon":
                result.home_positions[account.account_id] = adapter.home_position()
        except AdapterError as exc:
            result.errors.append(f"{account.label}: {exc}")
        except Exception as exc:
            result.errors.append(
                f"{account.label}: refresh failed ({type(exc).__name__}). "
                "Check the connection and account sign-in."
            )
    return result
