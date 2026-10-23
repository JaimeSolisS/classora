"""Registrations that every composition root makes (#40 D3, E4).

``administration`` imports no other module (it is the lower layer that
``identity`` and later modules call), so the approval types' handlers, the
notification types of the modules (#41; #52's offer types and its
``scheduling.offer_reassignment`` handler; #232's
``scheduling.occurrence_skipped_closure``; #54's three edit types; #55's
three substitution and cancellation types), the #55 cancellation workflow
step (``app.workflows.session_cancellation``), the
account-reference resolver of the approval inbox and the modules' time-zone
dependency checks (#37, #47: ``scheduling.class_sessions``; #53:
``scheduling.class_series``, and the ``scheduling.occurrence_conflict``
approval type; #59: ``scheduling.session_requests`` and the
``scheduling.session_request`` approval type; #60: the two session request
decision notification types; #62: the ``scheduling.change_request``
approval type, its two decision notification types and the
``scheduling.change_requests`` time-zone check; M4-18 #63: the
``staff.availability_change`` approval type
(``app.workflows.availability_change``), its two decision notification
types, ``scheduling.reassignment_needed`` and the scheduling implementation
of the ``staff`` availability impact seam) are wired here, by the API app factory
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
    register_availability_impact_source,
    register_availability_notifications,
    register_qualification_notifications,
)
from app.workflows.availability_change import AVAILABILITY_CHANGE_HANDLER
from app.workflows.session_cancellation import on_session_cancelled


def register_approval_wiring() -> None:
    """Every approval type's handler, the modules' notification types, the
    account-reference resolver, the time-zone dependency checks and the
    availability impact source.
    Registering again changes nothing."""
    administration_service.register_approval_handler(REGISTRATION_APPROVAL_HANDLER)
    administration_service.register_approval_handler(QUALIFICATION_APPROVAL_HANDLER)
    administration_service.register_approval_handler(
        scheduling_service.OCCURRENCE_CONFLICT_HANDLER
    )
    register_qualification_notifications()
    administration_service.register_approval_handler(
        scheduling_service.OFFER_REASSIGNMENT_HANDLER
    )
    administration_service.register_approval_handler(
        scheduling_service.SESSION_REQUEST_APPROVAL_HANDLER
    )
    administration_service.register_approval_handler(
        scheduling_service.CHANGE_REQUEST_APPROVAL_HANDLER
    )
    administration_service.register_approval_handler(AVAILABILITY_CHANGE_HANDLER)
    register_availability_notifications()
    scheduling_service.register_reassignment_notifications()
    register_availability_impact_source(scheduling_service.AVAILABILITY_IMPACT_SOURCE)
    scheduling_service.register_offer_notifications()
    scheduling_service.register_closure_notifications()
    scheduling_service.register_edit_notifications()
    scheduling_service.register_substitution_notifications()
    scheduling_service.register_request_notifications()
    scheduling_service.register_change_request_notifications()
    scheduling_service.register_cancellation_step(on_session_cancelled)
    administration_service.register_account_reference_resolver(get_account_references)
    administration_service.register_time_zone_dependency(
        scheduling_service.TIME_ZONE_DEPENDENCY, scheduling_service.has_class_sessions
    )
    administration_service.register_time_zone_dependency(
        scheduling_service.SERIES_TIME_ZONE_DEPENDENCY,
        scheduling_service.has_class_series,
    )
    administration_service.register_time_zone_dependency(
        scheduling_service.SESSION_REQUESTS_TIME_ZONE_DEPENDENCY,
        scheduling_service.has_pending_session_requests,
    )
    administration_service.register_time_zone_dependency(
        scheduling_service.CHANGE_REQUESTS_TIME_ZONE_DEPENDENCY,
        scheduling_service.has_pending_reschedule_requests,
    )
