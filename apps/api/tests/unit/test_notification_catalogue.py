"""The flags of every notification type (#119 A3, A5).

Each registered type says whether it is mandatory and which roles can
receive it (the preferences catalogue). This pin makes every row a reviewed
choice: a new type fails it until it is added below **and** to M8-02b's web
label pin (``NOTIFICATION_PREFERENCE_TYPES`` and
``notifications.preferences.types.<code>`` in es/en).
"""

from app.main import build_app
from app.modules.administration import service as administration
from app.modules.administration.notification_preferences import catalogue_types
from app.modules.administration.notifications_dev import DEV_DEMO_TYPE
from app.modules.identity.permissions import ROLE_CODES

S = frozenset({"owner", "administrator", "academic_coordinator", "finance_staff"})
ASSIGNERS = frozenset({"owner", "administrator", "academic_coordinator"})
T = frozenset({"tutor"})
F = frozenset({"student", "guardian"})
TF = T | F

OPTIONAL = False
MANDATORY = True

PINNED: dict[str, tuple[bool, frozenset[str]]] = {
    "academics.closeout_approved": (OPTIONAL, T),
    "academics.closeout_corrected": (OPTIONAL, T),
    "academics.closeout_changes_requested": (OPTIONAL, T),
    "academics.closeout_submitted_on_behalf": (OPTIONAL, T),
    "academics.learning_note_published": (OPTIONAL, F),
    "academics.learning_note_unpublished": (OPTIONAL, T),
    "academics.evaluation_finalized": (OPTIONAL, F),
    "academics.evaluation_voided": (OPTIONAL, T),
    "academics.learning_plan_activated": (OPTIONAL, F),
    "academics.learning_plan_completed": (OPTIONAL, F),
    "academics.assignment_set": (OPTIONAL, F),
    "academics.assignment_graded": (OPTIONAL, F),
    "academics.assignment_submitted": (OPTIONAL, T),
    "academics.progress_report_approved": (OPTIONAL, TF),
    "academics.progress_report_changes_requested": (OPTIONAL, T),
    "academics.progress_report_retracted": (OPTIONAL, T),
    "academics.progress_report_due": (OPTIONAL, T),
    "staff.qualification_approved": (OPTIONAL, T),
    "staff.qualification_rejected": (OPTIONAL, T),
    "staff.qualification_revoked": (OPTIONAL, T),
    "staff.availability_change_approved": (OPTIONAL, T),
    "staff.availability_change_rejected": (OPTIONAL, T),
    "scheduling.offer_received": (OPTIONAL, T),
    "scheduling.offer_declined": (OPTIONAL, ASSIGNERS),
    "scheduling.offer_expired": (OPTIONAL, ASSIGNERS),
    "scheduling.session_request_confirmed": (OPTIONAL, F),
    "scheduling.session_request_rejected": (OPTIONAL, F),
    "scheduling.change_request_approved": (OPTIONAL, TF),
    "scheduling.change_request_rejected": (OPTIONAL, TF),
    "scheduling.reassignment_needed": (OPTIONAL, ASSIGNERS),
    "scheduling.session_cancelled": (MANDATORY, TF),
    "scheduling.session_tutor_changed": (MANDATORY, F),
    "scheduling.tutor_substituted": (MANDATORY, T),
    "scheduling.session_rescheduled": (MANDATORY, TF),
    "scheduling.series_changed": (MANDATORY, TF),
    "scheduling.series_cancelled": (MANDATORY, TF),
    "scheduling.occurrence_skipped_closure": (MANDATORY, TF),
    "billing.payment_reminder": (OPTIONAL, frozenset({"guardian"})),
    "payroll.period_finalized": (OPTIONAL, T),
    "administration.approval_assigned": (OPTIONAL, S),
}


def _registered() -> dict[str, tuple[bool, frozenset[str]]]:
    build_app(include_dev_routes=True)
    found: dict[str, tuple[bool, frozenset[str]]] = {}
    for code in administration.registered_notification_types():
        info = administration.notification_type_info(code)
        assert info is not None
        found[code] = (info.mandatory, info.recipient_roles)
    return found


def test_every_registered_type_has_the_pinned_flags():
    registered = _registered()
    registered.pop(DEV_DEMO_TYPE, None)
    extra = sorted(set(registered) - set(PINNED))
    missing = sorted(set(PINNED) - set(registered))

    assert not extra, (
        f"new notification types {extra}: add each to PINNED with its mandatory"
        " flag and recipient roles, and to M8-02b's web label pin"
        " (NOTIFICATION_PREFERENCE_TYPES, notifications.preferences.types.<code>)"
    )
    assert not missing, f"pinned types no longer registered: {missing}"
    assert registered == PINNED


def test_the_pin_has_40_types_7_of_them_mandatory():
    assert len(PINNED) == 40
    assert sorted(code for code, (mandatory, _) in PINNED.items() if mandatory) == [
        "scheduling.occurrence_skipped_closure",
        "scheduling.series_cancelled",
        "scheduling.series_changed",
        "scheduling.session_cancelled",
        "scheduling.session_rescheduled",
        "scheduling.session_tutor_changed",
        "scheduling.tutor_substituted",
    ]


def test_every_type_names_only_real_roles():
    for code, (_, roles) in _registered().items():
        assert roles <= set(ROLE_CODES), code
        if code != DEV_DEMO_TYPE:
            assert roles, code


def test_dev_demo_is_in_no_catalogue():
    _registered()
    info = administration.notification_type_info(DEV_DEMO_TYPE)

    assert info is not None
    assert (info.mandatory, info.recipient_roles) == (False, frozenset())
    assert DEV_DEMO_TYPE not in catalogue_types(frozenset(ROLE_CODES))


def test_a_catalogue_is_the_union_of_the_callers_roles():
    _registered()
    guardian = set(catalogue_types(frozenset({"guardian"})))
    tutor = set(catalogue_types(T))

    assert "billing.payment_reminder" in guardian
    assert "payroll.period_finalized" not in guardian
    assert "billing.payment_reminder" not in tutor
    assert set(catalogue_types(frozenset({"guardian", "tutor"}))) == guardian | tutor
    assert "administration.approval_assigned" in catalogue_types(
        frozenset({"finance_staff"})
    )
    assert catalogue_types(frozenset()) == {}
