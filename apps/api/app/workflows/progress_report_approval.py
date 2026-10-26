"""The ``academics.progress_report`` approval type (#83 B4, E3; the #78/#312
pattern): a submitted progress report waits in the approval inbox, and
``progress_report.review`` holders decide it on its own review page.

**Decisions go through the report routes** (``POST
/api/v1/progress-reports/{id}/approve | request-changes | retract``, with the
report's ``version``): the route moves the report first and then resolves
the item through ``administration.service.decide_approval_request`` in the
same Unit of Work (lock order: the report, then the request). So
``approve`` accepts only a report already ``approved_for_family``,
``request_changes`` only one already ``changes_requested`` and ``reject``
only one already ``retracted`` (a retraction of a submitted report rejects
its item with the reason); otherwise they raise ``409
progress_report_review_route_required`` with ``details.review_path`` and
the item stays as it was. The report is read without a lock.

**Generic inbox routes** (``inbox_decisions = False``, #312
``inbox_route_error``): that same error for every request status, right
after the core's ``404`` check. The inbox lists no decisions for these
items, only the "Review" link (#83b).

**Summaries** (AM1): ``{report_id, student {id, display_name}, author
{display_name}, kind, period_start, period_end, subject {id, name} | null,
submitted_at}`` in three statements; never the narrative, the facts or a
reason.
"""

import uuid
from collections.abc import Iterable, Mapping
from typing import Any, Final

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.authz import Principal
from app.modules.academics import service as academics
from app.modules.administration import service as administration
from app.modules.catalog import service as catalog
from app.modules.families import service as families
from app.modules.identity import service as identity

PROGRESS_REPORT_TYPE: Final = "academics.progress_report"

rules = academics.progress_report_rules

SUMMARY_KEYS: Final = frozenset(
    {
        "report_id",
        "student",
        "author",
        "kind",
        "period_start",
        "period_end",
        "subject",
        "submitted_at",
    }
)


def review_path(report_id: uuid.UUID) -> str:
    """The review page of a progress report (#83 E3; the page is #83b's)."""
    return f"/progress-reports/{report_id}"


class ProgressReportApprovalHandler:
    """The :class:`~app.modules.administration.service.ApprovalHandler` of
    progress reports; the subject is the report (see the module
    docstring)."""

    request_type: Final = PROGRESS_REPORT_TYPE
    review_permission: Final = rules.PROGRESS_REPORT_REVIEW
    supports_request_changes: Final = True
    reason_required_on_reject: Final = True
    inbox_decisions: Final = False
    """E3: the inbox lists no decisions, only the "Review" link."""

    async def _route_error(
        self,
        session: AsyncSession,
        request: administration.ApprovalRequestView,
        status: str | None,
    ) -> academics.ProgressReportReviewRouteRequiredError | None:
        found = (
            await academics.reports_by_id(
                session, [request.subject_id], subjects=catalog.subject_name_columns()
            )
        ).get(request.subject_id)
        if found is None or status is None or found.report.status != status:
            return academics.ProgressReportReviewRouteRequiredError(
                review_path(request.subject_id)
            )
        return None

    async def _require(
        self,
        session: AsyncSession,
        request: administration.ApprovalRequestView,
        status: str | None,
    ) -> None:
        error = await self._route_error(session, request, status)
        if error is not None:
            raise error

    async def inbox_route_error(
        self,
        session: AsyncSession,
        request: administration.ApprovalRequestView,
    ) -> academics.ProgressReportReviewRouteRequiredError:
        """#312: the generic routes' answer for any request status."""
        error = await self._route_error(session, request, None)
        assert error is not None  # noqa: S101  (status None always refuses)
        return error

    async def approve(
        self,
        session: AsyncSession,
        request: administration.ApprovalRequestView,
        principal: Principal,
    ) -> None:
        await self._require(session, request, rules.APPROVED)

    async def reject(
        self,
        session: AsyncSession,
        request: administration.ApprovalRequestView,
        principal: Principal,
        reason: str | None,
    ) -> None:
        await self._require(session, request, rules.RETRACTED)

    async def request_changes(
        self,
        session: AsyncSession,
        request: administration.ApprovalRequestView,
        principal: Principal,
        reason: str,
    ) -> None:
        await self._require(session, request, rules.CHANGES_REQUESTED)

    async def summaries(
        self, session: AsyncSession, subject_ids: Iterable[uuid.UUID]
    ) -> Mapping[uuid.UUID, Mapping[str, Any]]:
        """AM1, by report id, in three statements (the reports with their
        subjects' names, the students, the authors)."""
        ids = set(subject_ids)
        if not ids:
            return {}
        rows = await academics.reports_by_id(
            session, ids, subjects=catalog.subject_name_columns()
        )
        students = await families.student_references(
            session, {row.report.student_id for row in rows.values()}
        )
        people = await identity.get_account_references(
            session, {row.report.author_user_id for row in rows.values()}
        )
        found: dict[uuid.UUID, Mapping[str, Any]] = {}
        for report_id, row in rows.items():
            report = row.report
            student = students.get(report.student_id)
            author = people.get(report.author_user_id)
            found[report_id] = {
                "report_id": str(report.id),
                "student": {
                    "id": str(report.student_id),
                    "display_name": "" if student is None else student.display_name,
                },
                "author": {
                    "display_name": "" if author is None else author.display_name
                },
                "kind": report.kind,
                "period_start": report.period_start.isoformat(),
                "period_end": report.period_end.isoformat(),
                "subject": (
                    None
                    if report.subject_id is None
                    else {"id": str(report.subject_id), "name": row.subject_name or ""}
                ),
                "submitted_at": (
                    None
                    if report.submitted_at is None
                    else report.submitted_at.isoformat()
                ),
            }
        return found


PROGRESS_REPORT_HANDLER: Final = ProgressReportApprovalHandler()
