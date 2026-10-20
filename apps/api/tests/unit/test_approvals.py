"""The approval inbox core without a database (#40 B1, B2, D3, E4)."""

import uuid
from collections.abc import Iterator
from typing import Any

import pytest

from app.main import build_app
from app.modules.administration import approvals
from app.modules.administration.models import APPROVAL_STATUSES
from app.modules.administration.service import (
    register_account_reference_resolver,
    register_approval_handler,
    registered_account_reference_resolver,
    registered_approval_types,
    unregister_approval_handler,
)
from app.modules.identity.service import (
    REGISTRATION_APPROVAL_HANDLER,
    get_account_references,
)

ALLOWED = {
    ("pending", "approved"),
    ("pending", "rejected"),
    ("pending", "changes_requested"),
    ("pending", "cancelled"),
    ("changes_requested", "pending"),
    ("changes_requested", "rejected"),
    ("changes_requested", "cancelled"),
}


@pytest.mark.parametrize("current", APPROVAL_STATUSES)
@pytest.mark.parametrize("target", APPROVAL_STATUSES)
def test_every_status_pair(current, target):
    allowed = (current, target) in ALLOWED

    assert approvals.transition_allowed(current, target) is allowed
    if allowed:
        approvals.ensure_transition(current, target)
    else:
        with pytest.raises(approvals.ApprovalTransitionInvalidError):
            approvals.ensure_transition(current, target)


def test_final_statuses_allow_nothing():
    for final in ("approved", "rejected", "cancelled"):
        assert not any(
            approvals.transition_allowed(final, target) for target in APPROVAL_STATUSES
        )


def test_an_unknown_status_allows_nothing():
    assert not approvals.transition_allowed("draft", "approved")


@pytest.mark.parametrize(
    ("status", "supports", "expected"),
    [
        ("pending", True, ["approve", "reject", "request_changes"]),
        ("pending", False, ["approve", "reject"]),
        ("changes_requested", True, ["reject"]),
        ("approved", True, []),
        ("rejected", True, []),
        ("cancelled", True, []),
    ],
)
def test_allowed_actions(status, supports, expected):
    assert (
        approvals.allowed_actions(status, supports_request_changes=supports) == expected
    )


class FakeHandler:
    def __init__(
        self, request_type: str = "test.unit", permission: str = "payroll.finalize"
    ) -> None:
        self.request_type = request_type
        self.review_permission = permission
        self.supports_request_changes = True
        self.reason_required_on_reject = False

    async def approve(self, *args: Any) -> None: ...

    async def reject(self, *args: Any) -> None: ...

    async def request_changes(self, *args: Any) -> None: ...

    async def summaries(self, *args: Any) -> dict[uuid.UUID, dict[str, Any]]:
        return {}


@pytest.fixture
def cleanup() -> Iterator[list[str]]:
    types: list[str] = []
    yield types
    for request_type in types:
        unregister_approval_handler(request_type)


def test_registering_the_same_handler_twice_is_a_no_op(cleanup):
    handler = FakeHandler()
    cleanup.append(handler.request_type)

    register_approval_handler(handler)
    register_approval_handler(handler)

    assert handler.request_type in registered_approval_types()


def test_another_handler_for_a_registered_type_raises(cleanup):
    cleanup.append("test.unit")
    register_approval_handler(FakeHandler())

    with pytest.raises(ValueError, match="already registered"):
        register_approval_handler(FakeHandler())


@pytest.mark.parametrize(
    "request_type", ["unit", "Test.unit", "test.unit.", "test." + "a" * 60]
)
def test_a_bad_type_is_refused(request_type):
    with pytest.raises(ValueError, match="not an approval request type"):
        register_approval_handler(FakeHandler(request_type))
    assert request_type not in registered_approval_types()


def test_a_bad_review_permission_is_refused():
    with pytest.raises(ValueError, match="not a permission code"):
        register_approval_handler(FakeHandler(permission="Payroll"))


def test_the_registration_handler_is_the_only_registered_type():
    # D3: build_app registers exactly the identity.registration type.
    unregister_approval_handler("identity.registration")
    build_app()

    assert registered_approval_types() == {"identity.registration"}
    handler = REGISTRATION_APPROVAL_HANDLER
    assert handler.review_permission == "registration.review"
    assert handler.supports_request_changes is False
    assert handler.reason_required_on_reject is False


def test_build_app_registers_the_account_reference_resolver():
    # E4: administration imports no module; the resolver comes from build_app.
    register_account_reference_resolver(None)
    try:
        build_app()
        assert registered_account_reference_resolver() is get_account_references
    finally:
        register_account_reference_resolver(get_account_references)


@pytest.mark.anyio
async def test_without_a_resolver_items_fail_closed():
    register_account_reference_resolver(None)
    try:
        with pytest.raises(approvals.AccountResolverMissingError):
            await approvals._account_names(None, [uuid.uuid4()])  # type: ignore[arg-type]
    finally:
        register_account_reference_resolver(get_account_references)


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


def test_the_cli_and_the_worker_register_the_wiring():
    from app import cli, worker

    for run in (
        lambda: pytest.raises(SystemExit, cli.main, ["bootstrap-owner"]),
        lambda: worker.main([]),
    ):
        unregister_approval_handler("identity.registration")
        register_account_reference_resolver(None)
        run()
        assert registered_approval_types() == {"identity.registration"}
        assert registered_account_reference_resolver() is get_account_references
