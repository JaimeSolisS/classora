"""Registrations that every composition root makes (#40 D3, E4).

``administration`` imports no other module (it is the lower layer that
``identity`` and later modules call), so the approval types' handlers and
the account-reference resolver of the approval inbox are wired here, by the
API app factory (``app.main.build_app``), ``app.cli`` and ``app.worker``.
"""

from app.modules.administration import service as administration_service
from app.modules.identity.service import (
    REGISTRATION_APPROVAL_HANDLER,
    get_account_references,
)


def register_approval_wiring() -> None:
    """Every approval type's handler and the account-reference resolver.
    Registering again changes nothing."""
    administration_service.register_approval_handler(REGISTRATION_APPROVAL_HANDLER)
    administration_service.register_account_reference_resolver(get_account_references)
