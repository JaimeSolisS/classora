"""The identity factory without a database (M2-02 I1)."""

from app.modules.identity.service import normalize_username, verify_password
from tests.factories.identity import TEST_USER_PASSWORD, UserFactory


def test_factory_hash_is_computed_once():
    first = UserFactory.build().password_hash
    second = UserFactory.build().password_hash

    assert first is second
    assert first.startswith("$argon2id$v=19$m=19456,t=2,p=1$")
    assert verify_password(first, TEST_USER_PASSWORD)


def test_built_users_are_active_with_valid_unique_usernames():
    users = UserFactory.build_batch(5)

    assert len({user.username for user in users}) == 5
    assert all(normalize_username(u.username) == u.username for u in users)
    assert all(user.state == "active" for user in users)
    assert all(user.display_name.strip() for user in users)
