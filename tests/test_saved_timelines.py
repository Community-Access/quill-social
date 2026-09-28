import pytest

from quill_social.model import Account, SocialItem
from quill_social.services.timelines import TimelineLibrary


def test_membership_preserves_home_and_existing_local_state(store):
    account = store.put_account(Account(network="mastodon"))
    library = TimelineLibrary(store)
    spec = library.create(account.account_id, "hashtag", "#accessibility")
    home = store.upsert_item(SocialItem(account_id=account.account_id, network="mastodon",
                                        remote_id="1", read=True, flagged=True))
    loaded = library.store_loaded(spec.scope, [
        SocialItem(account_id=account.account_id, network="mastodon", remote_id="1"),
        SocialItem(account_id=account.account_id, network="mastodon", remote_id="2"),
    ])
    assert loaded[0].item_id == home.item_id
    assert loaded[0].read and loaded[0].flagged
    assert store.get_document("timeline-only", home.item_id) is None
    assert store.get_document("timeline-only", loaded[1].item_id) == {}
    assert [i.remote_id for i in library.items(spec.scope)] == ["1", "2"]
    library.store_loaded(spec.scope, [loaded[1]])
    assert [i.remote_id for i in library.items(spec.scope)] == ["2"]
    assert store.get_item(home.item_id) is not None


def test_specs_deduplicate_filter_accounts_and_remove_only_view(store):
    one = store.put_account(Account(network="mastodon"))
    two = store.put_account(Account(network="mastodon"))
    library = TimelineLibrary(store)
    spec = library.create(one.account_id, "hashtag", "#test", announce=False)
    assert library.create(one.account_id, "hashtag", "test").scope == spec.scope
    library.create(two.account_id, "local")
    assert library.list(one.account_id) == [spec]
    assert library.get(spec.scope).announce is False
    library.remove(spec.scope)
    assert library.get(spec.scope) is None
    assert len(library.list()) == 1


def test_wrong_account_rejected_before_cache_changes(store):
    account = store.put_account(Account(network="mastodon"))
    library = TimelineLibrary(store)
    spec = library.create(account.account_id, "messages")
    with pytest.raises(ValueError, match="different account"):
        library.store_loaded(spec.scope, [SocialItem(account_id="other")])
    assert library.items(spec.scope) == []
    with pytest.raises(ValueError):
        library.create(account.account_id, "hashtag", "#")


def test_pruned_posts_are_not_returned(store):
    account = store.put_account(Account(network="mastodon"))
    library = TimelineLibrary(store)
    spec = library.create(account.account_id, "local")
    library.store_loaded(spec.scope, [SocialItem(account_id=account.account_id)])
    store.clear_timeline_cache()
    assert library.items(spec.scope) == []


def test_search_type_is_persisted_and_part_of_identity(store):
    account = store.put_account(Account(network="mastodon"))
    library = TimelineLibrary(store)
    posts = library.create(account.account_id, "search", "quill", search_type="statuses")
    users = library.create(account.account_id, "search", "quill", search_type="accounts")
    assert posts.scope != users.scope
    assert library.get(posts.scope).search_type == "statuses"
    assert library.create(account.account_id, "search", "quill",
                          search_type="statuses").scope == posts.scope
