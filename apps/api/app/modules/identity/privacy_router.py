"""``/api/v1/privacy-notice`` (#174; spec §9): read and accept the current
privacy notice.

Both routes are ``authenticated(notice_exempt=True)``: a user calls them
before accepting (every other protected route answers ``403
privacy_notice_required`` until then; ``app.core.authz``).

- ``GET /api/v1/privacy-notice``: the current version with both texts and the
  caller's acceptance time of it (or ``null``).
- ``POST /api/v1/privacy-notice/acceptance``: accept the version the client
  showed. Accepting again returns the first acceptance and writes nothing; a
  version that isn't current is ``409 privacy_notice_outdated``. Needs the
  app's ``Origin`` and the session's ``X-CSRF-Token`` (#24). No
  reauthentication.
"""

from typing import Annotated

from fastapi import APIRouter

from app.core.authz import AUTH_RESPONSES, CSRF_RESPONSES, Principal, authenticated
from app.core.errors import problem_response_doc
from app.core.uow import UnitOfWorkDep
from app.modules.identity import privacy
from app.modules.identity.schemas import (
    PrivacyNoticeAcceptanceRequest,
    PrivacyNoticeAccepted,
    PrivacyNoticeRead,
)

router = APIRouter(prefix="/api/v1/privacy-notice", tags=["privacy-notice"])


@router.get("", responses=AUTH_RESPONSES)
async def get_privacy_notice(
    principal: Annotated[Principal, authenticated(notice_exempt=True)],
    uow: UnitOfWorkDep,
) -> PrivacyNoticeRead:
    """The current privacy notice, in Spanish and English, and when the caller
    accepted it (``null`` when they haven't)."""
    notice = await privacy.current_privacy_notice(uow.session)
    acceptance = await privacy.find_privacy_notice_acceptance(
        uow.session, user_id=principal.user_id, notice_version_id=notice.id
    )
    return PrivacyNoticeRead(
        version=notice.version,
        published_at=notice.published_at,
        body_es=notice.body_es,
        body_en=notice.body_en,
        accepted_at=acceptance.accepted_at if acceptance is not None else None,
    )


@router.post(
    "/acceptance",
    responses={
        **AUTH_RESPONSES,
        **CSRF_RESPONSES,
        409: problem_response_doc(
            "The version isn't the current one: older, unknown or not yet"
            " published (privacy_notice_outdated). Nothing is written."
        ),
        422: problem_response_doc("The body is not valid (validation_error)."),
    },
)
async def accept_privacy_notice(
    principal: Annotated[Principal, authenticated(notice_exempt=True)],
    body: PrivacyNoticeAcceptanceRequest,
    uow: UnitOfWorkDep,
) -> PrivacyNoticeAccepted:
    """Accept the current privacy notice. ``version`` must be the version the
    user was shown, and the current one. Accepting again returns the first
    ``accepted_at``. Needs the app's ``Origin`` and the session's
    ``X-CSRF-Token``."""
    acceptance = await privacy.accept_privacy_notice(
        uow.session, user_id=principal.user_id, version=body.version
    )
    return PrivacyNoticeAccepted(
        version=body.version, accepted_at=acceptance.accepted_at
    )
