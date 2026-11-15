"""Self-registration requests (#169): a public request, reviewed by staff.

Public route (``public()``; ``Origin`` check only, #24; ``rate_limit(
"registration.ip")``, 10 per IP per hour, ADR-0008 D15):

- ``POST /api/v1/auth/registrations``: a pending user without a role and a
  pending request (``202``).

Staff review the requests in the approval inbox (#40): approval requests of
type ``identity.registration`` under ``/api/v1/approval-requests``
(``registration.review``; Owner and Administrator). The former
``/api/v1/registration-requests`` routes are gone. No reauthentication:
approval grants only Parent/Guardian or Student, like a non-staff invitation
(ADR-0007 D10).
"""

from fastapi import APIRouter

from app.core.authz import CSRF_RESPONSES, public
from app.core.errors import problem_response_doc
from app.core.ratelimit import RATE_LIMIT_RESPONSES, rate_limit
from app.core.uow import UnitOfWorkDep
from app.modules.identity import registrations
from app.modules.identity.schemas import RegistrationCreate, RegistrationSubmitted

router = APIRouter(tags=["registrations"])

REGISTRATION_PUBLIC_REASON = (
    "The person has no account yet; the request stays pending until staff"
    " approve it. CSRF protection is the Origin check plus SameSite (#24)"
)


@router.post(
    "/api/v1/auth/registrations",
    status_code=202,
    dependencies=[
        public(reason=REGISTRATION_PUBLIC_REASON),
        rate_limit("registration.ip"),
    ],
    responses={
        **CSRF_RESPONSES,
        409: problem_response_doc("The username is already taken (username_taken)."),
        422: problem_response_doc(
            "The body is not valid (validation_error; also any role other than"
            " guardian or student), the username is not valid"
            " (username_invalid), or the password breaks the policy"
            " (password_too_short, password_too_long, password_equals_username)."
        ),
        **RATE_LIMIT_RESPONSES,
    },
)
async def submit_registration(
    body: RegistrationCreate, uow: UnitOfWorkDep
) -> RegistrationSubmitted:
    """Request an account as a parent or guardian, or as a student.

    Creates a pending account that cannot sign in until an Owner or
    Administrator approves it. Never links or enrolls anyone. Needs the app's
    ``Origin``. Every request counts against the hourly limit.
    """
    await registrations.submit_registration(
        uow.session,
        username=body.username,
        display_name=body.display_name,
        password=body.password,
        role=body.role,
    )
    return RegistrationSubmitted(status="pending")
