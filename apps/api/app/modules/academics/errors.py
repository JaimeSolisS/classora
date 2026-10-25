"""Session closeout errors (#77 D2, E1; #78 C, D).

The routes also answer the shared ``404 not_found``
(:class:`~app.core.authz.RecordNotFoundError`, the same body for an unknown
and a hidden session) and ``409 version_conflict``; ``409
closeout_attendance_incomplete`` is raised by
``scheduling.service.complete_class_session``. ``details`` carry codes only,
never record data.
"""

from collections.abc import Sequence

from app.core.errors import AppError, FieldError


class CloseoutTutorOnlyError(AppError):
    """``403 closeout_tutor_only`` (D2 step 4): only the session's current
    tutor submits its closeout."""

    status = 403
    code = "closeout_tutor_only"
    detail = "Only the session's tutor submits its closeout."

    def __init__(self) -> None:
        super().__init__()


class CloseoutAlreadySubmittedError(AppError):
    """``409 closeout_already_submitted`` (A5): the session is ``completed``
    or already has a closeout."""

    status = 409
    code = "closeout_already_submitted"
    detail = "This session's closeout was already submitted."

    def __init__(self) -> None:
        super().__init__()


class SessionNotClosableError(AppError):
    """``409 session_not_closable`` (A5): the session isn't ``confirmed``;
    ``details`` is ``{"state": <state>}``."""

    status = 409
    code = "session_not_closable"
    detail = "This session can't be closed out."

    def __init__(self, state: str) -> None:
        super().__init__(details={"state": state})


class SessionNotStartedError(AppError):
    """``409 session_not_started`` (A5): the class hasn't started yet."""

    status = 409
    code = "session_not_started"
    detail = "The closeout opens when the class starts."

    def __init__(self) -> None:
        super().__init__()


class CloseoutSummaryInvalidError(AppError):
    """``422 validation_error`` (D2 step 9): a required summary field is
    missing (``summary_required`` at ``body.summary.<field>``); #78 also
    raises it for a correction request without a reason
    (``reason_required`` at ``body.reason``)."""

    status = 422
    code = "validation_error"
    detail = "The request is not valid."

    def __init__(self, errors: Sequence[FieldError]) -> None:
        super().__init__(errors=errors)


class CloseoutReviewRouteRequiredError(AppError):
    """``409 closeout_review_route_required`` (#78 C4, E2, E3): the inbox's
    generic approve, reject or request changes can't decide a closeout;
    ``details.review_path`` is the staff session page, where the reviewer
    decides with the closeout's version. The item stays as it was."""

    status = 409
    code = "closeout_review_route_required"
    detail = "Review this closeout on its session page."

    def __init__(self, review_path: str) -> None:
        super().__init__(details={"review_path": review_path})


class CloseoutNotSubmittedError(AppError):
    """``409 closeout_not_submitted`` (#78 C1 step 8): only a ``submitted``
    closeout is approved or sent back."""

    status = 409
    code = "closeout_not_submitted"
    detail = "This closeout isn't waiting for review."

    def __init__(self) -> None:
        super().__init__()


class CloseoutWithTutorError(AppError):
    """``409 closeout_with_tutor`` (#78 C3, E5): changes were requested, so
    the tutor owns the record until they resubmit."""

    status = 409
    code = "closeout_with_tutor"
    detail = "Changes were requested: the tutor is editing this closeout."

    def __init__(self) -> None:
        super().__init__()


class CloseoutUnderReviewError(AppError):
    """``409 closeout_under_review`` (#78 D2 step 7): the closeout is
    ``submitted``; the tutor edits it only after changes are requested or
    asks for a correction after approval."""

    status = 409
    code = "closeout_under_review"
    detail = "This closeout is already under review."

    def __init__(self) -> None:
        super().__init__()


class CloseoutSelfReviewError(AppError):
    """``403 closeout_self_review`` (#78 E7): a reviewer can't approve,
    send back or correct a closeout they submitted (the Owner can)."""

    status = 403
    code = "closeout_self_review"
    detail = "You can't review your own closeout."

    def __init__(self) -> None:
        super().__init__()


class CloseoutUnchangedError(AppError):
    """``422 closeout_unchanged`` (#78 C3 step 4, D2 step 11): the
    correction changes nothing."""

    status = 422
    code = "closeout_unchanged"
    detail = "Nothing changed."

    def __init__(self) -> None:
        super().__init__()


# --- On behalf of the tutor (#301) --------------------------------------------------


class CloseoutOnBehalfOwnSessionError(AppError):
    """``403 closeout_on_behalf_own_session`` (#301 E5): a reviewer doesn't
    close out on behalf a session they tutor themselves (their own hours go
    through the tutor route)."""

    status = 403
    code = "closeout_on_behalf_own_session"
    detail = "You tutor this session: close it out as its tutor."

    def __init__(self) -> None:
        super().__init__()


class SessionNotEndedError(AppError):
    """``409 session_not_ended`` (#301 E2): a closeout on the tutor's behalf
    waits until the class has ended."""

    status = 409
    code = "session_not_ended"
    detail = "The class hasn't ended yet."

    def __init__(self) -> None:
        super().__init__()


class CloseoutNotWithTutorError(AppError):
    """``409 closeout_not_with_tutor`` (#301 D2 step 8): only a closeout
    sent back to its tutor (``changes_requested``) is taken over."""

    status = 409
    code = "closeout_not_with_tutor"
    detail = "This closeout isn't waiting for its tutor."

    def __init__(self) -> None:
        super().__init__()


class CloseoutSubmittedOnBehalfError(AppError):
    """``409 closeout_submitted_on_behalf`` (#301 E5): a closeout submitted on
    the tutor's behalf isn't sent back (the tutor may be gone); reviewers
    correct it and approve."""

    status = 409
    code = "closeout_submitted_on_behalf"
    detail = (
        "This closeout was submitted on the tutor's behalf: correct it instead"
        " of sending it back."
    )

    def __init__(self) -> None:
        super().__init__()


# --- Session notes (#79) ----------------------------------------------------------
# ``details`` carry codes only, never note text.


class NoteTutorOnlyError(AppError):
    """``403 note_tutor_only`` (#79 C2 step 4): a reviewer who isn't the
    session's current tutor doesn't write notes."""

    status = 403
    code = "note_tutor_only"
    detail = "Only the session's tutor writes notes."

    def __init__(self) -> None:
        super().__init__()


class NoteAuthorOnlyError(AppError):
    """``403 note_author_only`` (#79 D1 step 4): only the note's author
    edits, shares or deletes it (a reviewer may stop sharing it)."""

    status = 403
    code = "note_author_only"
    detail = "Only the note's author can do this."

    def __init__(self) -> None:
        super().__init__()


class NoteReadOnlyError(AppError):
    """``403 note_read_only`` (#79 E3): the student is no longer one of the
    tutor's current assignments; their notes stay readable, read-only."""

    status = 403
    code = "note_read_only"
    detail = "This student is no longer assigned to you: notes are read-only."

    def __init__(self) -> None:
        super().__init__()


class NotePublishedError(AppError):
    """``409 note_published`` (#79 D2, D5): a shared note is read-only and
    can't be deleted; stop sharing it first."""

    status = 409
    code = "note_published"
    detail = "This note is shared with the family: stop sharing it first."

    def __init__(self) -> None:
        super().__init__()


class NoteAlreadyPublishedError(AppError):
    """``409 note_already_published`` (#79 D3)."""

    status = 409
    code = "note_already_published"
    detail = "This note is already shared with the family."

    def __init__(self) -> None:
        super().__init__()


class NoteNotPublishedError(AppError):
    """``409 note_not_published`` (#79 D4)."""

    status = 409
    code = "note_not_published"
    detail = "This note isn't shared with the family."

    def __init__(self) -> None:
        super().__init__()


class SessionNotNotableError(AppError):
    """``409 session_not_notable`` (#79 E5): notes go on confirmed or
    completed sessions only; ``details`` is ``{"state": <state>}``."""

    status = 409
    code = "session_not_notable"
    detail = "Notes can't be written on this session."

    def __init__(self, state: str) -> None:
        super().__init__(details={"state": state})


class NoteFieldError(AppError):
    """``422 validation_error`` with one field error (#79 C2 step 7, D4):
    ``student_not_participant`` at ``body.student_id`` (also for an unknown
    student) or ``reason_required`` at ``body.reason``."""

    status = 422
    code = "validation_error"
    detail = "The request is not valid."

    def __init__(self, field: str, code: str, message: str) -> None:
        super().__init__(
            errors=[{"path": ["body", field], "code": code, "message": message}]
        )


# --- Evaluations (#80) -------------------------------------------------------------
# ``details`` carry codes and positions only, never scores or text.


class EvaluationTutorOnlyError(AppError):
    """``403 evaluation_tutor_only`` (#80 D2 step 4): an
    ``evaluation.manage`` holder who isn't a tutor with the student assigned
    doesn't record evaluations (the scores are the tutor's)."""

    status = 403
    code = "evaluation_tutor_only"
    detail = "Only a tutor currently teaching the student records evaluations."

    def __init__(self) -> None:
        super().__init__()


class EvaluationAuthorOnlyError(AppError):
    """``403 evaluation_author_only`` (#80 D3 step 4)."""

    status = 403
    code = "evaluation_author_only"
    detail = "Only the evaluation's author can do this."

    def __init__(self) -> None:
        super().__init__()


class EvaluationReadOnlyError(AppError):
    """``403 evaluation_read_only`` (#80 E5): the student is no longer one of
    the author's current assignments; their evaluations stay readable."""

    status = 403
    code = "evaluation_read_only"
    detail = "This student is no longer assigned to you: evaluations are read-only."

    def __init__(self) -> None:
        super().__init__()


class EvaluationTemplateInactiveError(AppError):
    """``409 template_inactive`` (#80 D2 step 5)."""

    status = 409
    code = "template_inactive"
    detail = "This evaluation template is inactive."

    def __init__(self) -> None:
        super().__init__()


class EvaluationTemplateNameTakenError(AppError):
    """``409 duplicate_name`` (#80 C1): template names are unique ignoring
    case (inactive templates count)."""

    status = 409
    code = "duplicate_name"
    detail = "Another evaluation template has this name."

    def __init__(self) -> None:
        super().__init__()


class SessionNotEvaluableError(AppError):
    """``409 session_not_evaluable`` (#80 D2 step 7): a linked session must
    be confirmed or completed and have started; ``details`` is ``{"state":
    <state>}``."""

    status = 409
    code = "session_not_evaluable"
    detail = "This class can't be evaluated yet."

    def __init__(self, state: str) -> None:
        super().__init__(details={"state": state})


class EvaluationFinalError(AppError):
    """``409 evaluation_final`` (#80 D7): a final evaluation can't be
    deleted."""

    status = 409
    code = "evaluation_final"
    detail = "A final evaluation can't be deleted."

    def __init__(self) -> None:
        super().__init__()


class EvaluationNotDraftError(AppError):
    """``409 evaluation_not_draft`` (#80 D5)."""

    status = 409
    code = "evaluation_not_draft"
    detail = "This evaluation is already final."

    def __init__(self) -> None:
        super().__init__()


class EvaluationNotFinalError(AppError):
    """``409 evaluation_not_final`` (#80 D6)."""

    status = 409
    code = "evaluation_not_final"
    detail = "Only a final evaluation can be voided."

    def __init__(self) -> None:
        super().__init__()


class EvaluationVoidedError(AppError):
    """``409 evaluation_voided`` (#80 D4, D6)."""

    status = 409
    code = "evaluation_voided"
    detail = "This evaluation was voided."

    def __init__(self) -> None:
        super().__init__()


class EvaluationIncompleteError(AppError):
    """``409 evaluation_incomplete`` (#80 D5): every criterion needs a score
    first; ``details`` is ``{"missing_positions": ["<position>", …]}`` (the
    positions as strings: ``details`` values are strings or string lists)."""

    status = 409
    code = "evaluation_incomplete"
    detail = "Score every criterion before finalizing."

    def __init__(self, missing_positions: Sequence[int]) -> None:
        super().__init__(
            details={"missing_positions": [str(p) for p in missing_positions]}
        )


class EvaluationFieldError(AppError):
    """``422 validation_error`` with field errors (#80 C1, D2, D4, D6, F2):
    the content checks that need stored data (``template_unknown``,
    ``subject_mismatch``, ``class_session_invalid``, ``score_out_of_range``,
    …). ``errors`` items are ``(path, code, message)``, for example
    ``(["body", "scores", 0, "score"], "score_out_of_range", "…")``."""

    status = 422
    code = "validation_error"
    detail = "The request is not valid."

    def __init__(self, errors: Sequence[tuple[Sequence[str | int], str, str]]) -> None:
        items: list[FieldError] = [
            {"path": list(path), "code": code, "message": message}
            for path, code, message in errors
        ]
        super().__init__(errors=items)

    @classmethod
    def one(cls, field: str, code: str, message: str) -> EvaluationFieldError:
        """One error at ``["body", field]``."""
        return cls([(["body", field], code, message)])
