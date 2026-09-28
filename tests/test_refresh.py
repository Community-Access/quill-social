from quill_social.model import Account, SocialItem
from quill_social.services import refresh


def test_adapter_construction_failure_does_not_block_other_accounts(monkeypatch):
    class Good:
        def home_timeline(self, **kwargs):
            return [SocialItem(text="still available")]

        def notifications(self, **kwargs):
            return []

    def adapter(account, credentials):
        if account.account_id == "broken":
            raise RuntimeError("vault unavailable")
        return Good()

    monkeypatch.setattr(refresh, "adapter_for", adapter)
    result = refresh.fetch_accounts([
        Account(account_id="broken", handle="@broken"),
        Account(account_id="good", handle="@good"),
    ], None)
    assert result.posts == 1
    assert result.items[0].account_id == "good"
    assert len(result.errors) == 1
    assert "@broken" in result.errors[0]


def test_search_filters_account_before_applying_limit(store):
    for i in range(4):
        store.upsert_item(SocialItem(account_id="first", remote_id=str(i), text="needle", created_at=i + 10))
    wanted = store.upsert_item(SocialItem(account_id="second", text="needle", created_at=1))
    assert store.search_items("needle", account_id="second", limit=1)[0].item_id == wanted.item_id
