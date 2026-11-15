"""Record and field policies of tutor profiles (#43 B2, B3; owner decision
E1 (a)).

Pure functions, no I/O: the service passes in the caller's
:class:`~app.core.authz.Principal` and, for edits, whether the identity rule
lets the caller manage the target's account.

**Who sees a tutor** (:func:`can_view_tutor`; the routes already require
``tutor.view``, held by Owners, Administrators, Academic Coordinators and
Tutors): a holder of ``tutor.manage`` and a holder of the
``academic_coordinator`` role see every tutor; anyone else (a caller who
holds ``tutor.view`` only through ``tutor``) sees only themselves. A
multi-role user gets the union. A tutor who isn't visible is ``404
not_found``, like a missing one; the list holds only the visible ones.

**What they may edit** (:func:`editable_fields`): with ``tutor.manage``
every field (:data:`ALL_FIELDS`) of any tutor; the tutor themselves their
contact fields and experience (:data:`SELF_FIELDS`); nobody else anything
(an Academic Coordinator views only). Sending a field outside the set is
``403 permission_denied``.

The account fields (display name, email, phone) of **another** user also
follow the #31 rule (``identity.service.may_manage_user``, ADR-0007 D4 as
amended by #31): an Administrator doesn't change an Owner's or another
Administrator's account here either (#43 decision D1). Their experience
stays editable.

The role and permission codes are the identity catalog's, repeated here
because this module imports no other module's internals (ADR-0002);
``tests/unit/test_tutor_policy.py`` checks they match.
"""

import uuid
from typing import Final, Literal

from app.core.authz import Principal

ACADEMIC_COORDINATOR: Final = "academic_coordinator"
TUTOR: Final = "tutor"

TUTOR_VIEW: Final = "tutor.view"
TUTOR_MANAGE: Final = "tutor.manage"

type TutorField = Literal["display_name", "email", "phone", "professional_experience"]

ACCOUNT_FIELDS: Final[frozenset[TutorField]] = frozenset(
    {"display_name", "email", "phone"}
)
"""Fields stored on the account (``users``), changed through
``identity.service.update_account_contact``."""

ALL_FIELDS: Final[frozenset[TutorField]] = frozenset(
    {"display_name", "email", "phone", "professional_experience"}
)
SELF_FIELDS: Final[frozenset[TutorField]] = frozenset(
    {"email", "phone", "professional_experience"}
)
NO_FIELDS: Final[frozenset[TutorField]] = frozenset()


def sees_every_tutor(principal: Principal) -> bool:
    """A holder of ``tutor.manage``, or of the ``academic_coordinator`` role
    together with ``tutor.view``: every tutor is visible."""
    if principal.has(TUTOR_MANAGE):
        return True
    return ACADEMIC_COORDINATOR in principal.roles and principal.has(TUTOR_VIEW)


def can_view_tutor(principal: Principal, target_user_id: uuid.UUID) -> bool:
    """Whether the caller may see the tutor ``target_user_id`` at all."""
    if not principal.has(TUTOR_VIEW):
        return False
    return sees_every_tutor(principal) or principal.user_id == target_user_id


def editable_fields(
    principal: Principal,
    target_user_id: uuid.UUID,
    *,
    may_manage_account: bool = True,
) -> frozenset[TutorField]:
    """The fields the caller may change on this tutor's profile.

    ``may_manage_account``: whether ``identity.service.may_manage_user``
    lets the caller manage the target's account (only consulted for a
    holder of ``tutor.manage`` editing someone else).
    """
    if not can_view_tutor(principal, target_user_id):
        return NO_FIELDS
    if principal.has(TUTOR_MANAGE):
        if principal.user_id == target_user_id or may_manage_account:
            return ALL_FIELDS
        return frozenset(ALL_FIELDS - ACCOUNT_FIELDS)
    if principal.user_id == target_user_id:
        return SELF_FIELDS
    return NO_FIELDS


def can_edit_availability(principal: Principal, target_user_id: uuid.UUID) -> bool:
    """Whether the caller may change the tutor's availability (#45): the
    tutor themselves or a holder of ``tutor.manage``; an Academic
    Coordinator views only."""
    if not can_view_tutor(principal, target_user_id):
        return False
    return principal.has(TUTOR_MANAGE) or principal.user_id == target_user_id
