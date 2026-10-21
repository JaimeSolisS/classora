"""Registrations that every composition root makes (#40 D3, E4).

``administration`` imports no other module (it is the lower layer that
``identity`` and later modules call), so the approval types' handlers, the
notification types of the modules (#41), the account-reference resolver
of the approval inbox and the modules' time-zone dependency checks (#37,
#47: ``scheduling.class_sessions``) are wired here, by the API app factory
(``app.main.build_app``), ``app.cli`` and ``app.worker``.
"""

from app.modules.administration import service as administration_service
from app.modules.identity.service import (
    REGISTRATION_APPROVAL_HANDLER,
    get_account_references,
)
from app.modules.scheduling import service as scheduling_service
from app.modules.staff.service import (
    QUALIFICATION_APPROVAL_HANDLER,
    register_qualification_notifications,
)


def register_approval_wiring() -> None:
    """Every approval type's handler, the modules' notification types, the
    account-reference resolver and the time-zone dependency checks.
    Registering again changes nothing."""
    administration_service.register_approval_handler(REGISTRATION_APPROVAL_HANDLER)
    administration_service.register_approval_handler(QUALIFICATION_APPROVAL_HANDLER)
    register_qualification_notifications()
    administration_service.register_account_reference_resolver(get_account_references)
    administration_service.register_time_zone_dependency(
        scheduling_service.TIME_ZONE_DEPENDENCY, scheduling_service.has_class_sessions
    )
