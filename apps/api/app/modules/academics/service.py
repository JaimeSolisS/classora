"""The academics module's public API (ADR-0002; #77 M5-10).

Session closeouts (:mod:`~app.modules.academics.closeouts`, the pure rules
in :mod:`~app.modules.academics.closeout_rules`): the ``session_closeouts``
row (:class:`SessionCloseout`), :func:`create_session_closeout`,
:func:`closeout_for_session`, :func:`closeouts_by_id`,
:func:`closeout_session_keys` (joined by ``scheduling`` into the inbox
summaries), :func:`summary_of`; the rules :func:`closeout_block`,
:func:`summary_errors`, :func:`all_marked_absent`, the record policy
:func:`closeout_access` and :func:`sees_summary`; and the errors.
``app.workflows.session_closeout`` composes them with
``scheduling.service.complete_class_session`` and
``administration.service.create_approval_request``.

Closeout review (#78, :mod:`~app.modules.academics.closeout_review`): the
closeout lock :func:`lock_closeout`, the immutable approval revisions
(:func:`add_approval`, :func:`current_approval`, :func:`closeout_history`),
the staff corrections (:func:`add_correction`), the E6 attendance unlock
predicate :func:`closeout_awaits_tutor` (registered by ``app.wiring``) and
the pure rules :func:`review_block`, :func:`is_self_review`,
:func:`corrected_fields`, :func:`attendance_changed`;
``app.workflows.session_closeout_review`` composes them.

Closeout on behalf of a tutor (#301): the rule :func:`on_behalf_block`, the
widened :func:`is_self_review`, the bodies :class:`CloseoutOnBehalfSubmit`
and :class:`CloseoutTakeOver` and their errors;
``app.workflows.closeout_on_behalf`` composes them.

Session notes (#79, :mod:`~app.modules.academics.notes`, the pure rules in
:mod:`~app.modules.academics.note_rules`): the ``session_notes`` row
(:class:`SessionNote`), :func:`create_note`, :func:`note_by_id`,
:func:`get_note_for_update`, :func:`update_note_body`,
:func:`publish_note`, :func:`unpublish_note`, :func:`soft_delete_note` and
the lists :func:`notes_for_session`, :func:`notes_for_student` and
:func:`family_notes_for_student` (the only family read: shared, live
notes); the rules module itself (:data:`note_rules`) and the errors.
``app.workflows.session_notes`` composes them with ``scheduling``,
``families`` and ``administration``.

Evaluations (#80, :mod:`~app.modules.academics.evaluation_templates`,
:mod:`~app.modules.academics.evaluations`, the pure rules in
:mod:`~app.modules.academics.evaluation_rules`): the template rows
(:func:`template_with_criteria`, :func:`list_templates`,
:func:`create_template`, :func:`replace_template`), the evaluation rows
(:func:`evaluation_by_id`, :func:`evaluation_for_update`,
:func:`scores_of`, :func:`create_evaluation`, :func:`change_evaluation`,
:func:`finalize_evaluation`, :func:`void_evaluation`,
:func:`soft_delete_evaluation`), the lists :func:`working_page`,
:func:`has_own_evaluation` and :func:`history_rows` (the only family read:
final, non-voided, live evaluations); the rules module
(:data:`evaluation_rules`) and the errors. ``app.workflows.evaluations``
composes them with ``scheduling``, ``catalog``, ``courses``, ``families``
and ``administration``. #85 course completion reads
:func:`cohort_assessment_evaluations` (the latest counted evaluation per
student and course assessment of a cohort).

Learning plans (#81, :mod:`~app.modules.academics.learning_plans`, the
pure rules in :mod:`~app.modules.academics.learning_plan_rules`): the rows
(:class:`LearningPlan`, :class:`LearningPlanObjective`,
:class:`LearningPlanMilestone`), :func:`create_plan`,
:func:`get_plan_for_update`, :func:`get_plan_for_share`,
:func:`replace_plan_content`, :func:`transition_plan`,
:func:`get_milestone_for_update`, :func:`set_milestone_status`,
:func:`soft_delete_plan`, the reads :func:`plan_with_subject`,
:func:`plan_items`, :func:`list_plans` (families: only
:func:`family_visible_condition` rows), :func:`milestone_facts`,
:func:`has_own_plan`, :func:`milestone_plan_id` and
:func:`other_active_plan_exists`; the rules module
(:data:`learning_plan_rules`) and the errors.
``app.workflows.learning_plans`` composes them with ``scheduling``,
``catalog``, ``families``, ``identity`` and ``administration``.

Homework assignments (#82, :mod:`~app.modules.academics.assignments`, the
pure rules in :mod:`~app.modules.academics.assignment_rules`): the rows
(:class:`Assignment`, :class:`AssignmentSubmission`), the reads
:func:`assignment_with_subject`, :func:`submissions_of`,
:func:`submission_facts`, :func:`submission_for`, :func:`student_facts`,
:func:`student_rows` (only the student's own row), :func:`has_own_assignment`,
:func:`assignment_working_page` and :func:`history_grades` (scored grades of
live assignments); the locks :func:`get_assignment_for_update`,
:func:`get_submission_for_update`, :func:`submissions_for_update`; the writes
:func:`create_assignment`, :func:`add_recipients`, :func:`remove_recipients`,
:func:`change_assignment`, :func:`soft_delete_assignment`,
:func:`record_submission`, :func:`grade_submission`; the rules module
(:data:`assignment_rules`) and the errors. ``app.workflows.assignments``
composes them with ``scheduling``, ``catalog``, ``families``, ``identity``
and ``administration``.

Progress reports (#83, :mod:`~app.modules.academics.progress_reports`, the
facts compiler :mod:`~app.modules.academics.progress_report_facts`, the pure
rules in :mod:`~app.modules.academics.progress_report_rules`): the row
(:class:`ProgressReport`), the reads :func:`report_with_subject`,
:func:`reports_by_id`, :func:`progress_report_working_page`,
:func:`approved_page` and :func:`approved_report` (the only family reads:
approved, live reports), :func:`has_own_approved_report`; the lock
:func:`get_report_for_update`; the writes :func:`create_report`,
:func:`change_narrative`, :func:`submit_report`, :func:`approve_report`,
:func:`request_report_changes`, :func:`retract_report`,
:func:`soft_delete_report`; the facts :func:`academic_facts` and
:func:`facts_document`; the rules module (:data:`progress_report_rules`) and
the errors. ``app.workflows.progress_reports`` composes them with
``scheduling``, ``catalog``, ``families``, ``identity`` and
``administration``.

Progress report schedules (#316,
:mod:`~app.modules.academics.progress_report_schedules`, the pure rules in
:mod:`~app.modules.academics.progress_report_schedule_rules`): the row
(:class:`ProgressReportSchedule`), :func:`schedules_for_student`,
:func:`get_schedule_for_update`, :func:`create_schedule`,
:func:`deactivate_schedule`, :func:`reactivate_schedule`, the period reads
:func:`period_reports` / :func:`split_period_reports` (:class:`PeriodKey`),
the job's :func:`due_schedule_ids` and :func:`insert_placeholder`; the rules
module (:data:`progress_report_schedule_rules`) and the errors.
``app.workflows.progress_report_schedules`` (routes) and
``app.workflows.progress_report_schedule_tasks`` (the job) compose them.

Progress dashboard reads (#84, :mod:`~app.modules.academics.progress_aggregates`,
shared with the facts compiler; the pure rules in
:mod:`~app.modules.academics.progress_dashboard_rules`): the counted
evaluations (:func:`evaluation_scores`), the homework rows due or graded in
a range (:func:`homework_rows`, :func:`homework_state_counts`,
:func:`is_counted_grade`), the listed plans (:func:`dashboard_plans`,
:func:`plan_counts`), the approved closeout summaries
(:func:`approved_summary_columns`), a tutor's closeouts as a select
(:func:`closeout_sessions_of`) and the shared note counts
(:func:`family_note_count_columns`; all three unexecuted selects that
``scheduling`` joins). ``app.workflows.progress_dashboard`` composes
them with ``scheduling``, ``catalog``, ``families``, ``identity`` and
``administration``.

Every function runs in the caller's Unit of Work and never commits.
"""

from app.modules.academics import assignment_rules as assignment_rules
from app.modules.academics import evaluation_rules as evaluation_rules
from app.modules.academics import learning_plan_rules as learning_plan_rules
from app.modules.academics import note_rules as note_rules
from app.modules.academics import (
    progress_dashboard_rules as progress_dashboard_rules,
)
from app.modules.academics import progress_report_facts as progress_report_facts
from app.modules.academics import progress_report_rules as progress_report_rules
from app.modules.academics import (
    progress_report_schedule_rules as progress_report_schedule_rules,
)
from app.modules.academics.assignments import AssignmentRow as AssignmentRow
from app.modules.academics.assignments import (
    AssignmentWorkingPage as AssignmentWorkingPage,
)
from app.modules.academics.assignments import GradeRow as GradeRow
from app.modules.academics.assignments import StudentFact as StudentFact
from app.modules.academics.assignments import StudentRow as StudentRow
from app.modules.academics.assignments import SubmissionFact as SubmissionFact
from app.modules.academics.assignments import add_recipients as add_recipients
from app.modules.academics.assignments import (
    assignment_with_subject as assignment_with_subject,
)
from app.modules.academics.assignments import (
    assignment_working_page as assignment_working_page,
)
from app.modules.academics.assignments import change_assignment as change_assignment
from app.modules.academics.assignments import create_assignment as create_assignment
from app.modules.academics.assignments import (
    get_assignment_for_update as get_assignment_for_update,
)
from app.modules.academics.assignments import (
    get_submission_for_update as get_submission_for_update,
)
from app.modules.academics.assignments import grade_submission as grade_submission
from app.modules.academics.assignments import has_own_assignment as has_own_assignment
from app.modules.academics.assignments import history_grades as history_grades
from app.modules.academics.assignments import record_submission as record_submission
from app.modules.academics.assignments import remove_recipients as remove_recipients
from app.modules.academics.assignments import (
    soft_delete_assignment as soft_delete_assignment,
)
from app.modules.academics.assignments import student_facts as student_facts
from app.modules.academics.assignments import student_rows as student_rows
from app.modules.academics.assignments import submission_facts as submission_facts
from app.modules.academics.assignments import submission_for as submission_for
from app.modules.academics.assignments import (
    submissions_for_update as submissions_for_update,
)
from app.modules.academics.assignments import submissions_of as submissions_of
from app.modules.academics.closeout_review import (
    ApprovalSnapshot as ApprovalSnapshot,
)
from app.modules.academics.closeout_review import CloseoutHistory as CloseoutHistory
from app.modules.academics.closeout_review import add_approval as add_approval
from app.modules.academics.closeout_review import add_correction as add_correction
from app.modules.academics.closeout_review import (
    closeout_awaits_tutor as closeout_awaits_tutor,
)
from app.modules.academics.closeout_review import (
    closeout_history as closeout_history,
)
from app.modules.academics.closeout_review import (
    current_approval as current_approval,
)
from app.modules.academics.closeout_review import lock_closeout as lock_closeout
from app.modules.academics.closeout_rules import (
    ACTUAL_MINUTES_RANGE as ACTUAL_MINUTES_RANGE,
)
from app.modules.academics.closeout_rules import (
    ALREADY_SUBMITTED as ALREADY_SUBMITTED,
)
from app.modules.academics.closeout_rules import APPROVED as APPROVED
from app.modules.academics.closeout_rules import (
    CHANGES_REQUESTED as CHANGES_REQUESTED,
)
from app.modules.academics.closeout_rules import NOT_CLOSABLE as NOT_CLOSABLE
from app.modules.academics.closeout_rules import NOT_ENDED as NOT_ENDED
from app.modules.academics.closeout_rules import NOT_STARTED as NOT_STARTED
from app.modules.academics.closeout_rules import NOT_SUBMITTED as NOT_SUBMITTED
from app.modules.academics.closeout_rules import (
    ON_BEHALF_OWN_SESSION as ON_BEHALF_OWN_SESSION,
)
from app.modules.academics.closeout_rules import SUBMITTED as SUBMITTED
from app.modules.academics.closeout_rules import (
    SUMMARY_REQUIRED as SUMMARY_REQUIRED,
)
from app.modules.academics.closeout_rules import (
    TEACHING_HOURS_APPROVE as TEACHING_HOURS_APPROVE,
)
from app.modules.academics.closeout_rules import TUTOR_ONLY as TUTOR_ONLY
from app.modules.academics.closeout_rules import UNDER_REVIEW as UNDER_REVIEW
from app.modules.academics.closeout_rules import WITH_TUTOR as WITH_TUTOR
from app.modules.academics.closeout_rules import CloseoutAccess as CloseoutAccess
from app.modules.academics.closeout_rules import CloseoutBlock as CloseoutBlock
from app.modules.academics.closeout_rules import OnBehalfBlock as OnBehalfBlock
from app.modules.academics.closeout_rules import (
    all_marked_absent as all_marked_absent,
)
from app.modules.academics.closeout_rules import (
    attendance_changed as attendance_changed,
)
from app.modules.academics.closeout_rules import closeout_access as closeout_access
from app.modules.academics.closeout_rules import closeout_block as closeout_block
from app.modules.academics.closeout_rules import (
    corrected_fields as corrected_fields,
)
from app.modules.academics.closeout_rules import (
    is_current_tutor as is_current_tutor,
)
from app.modules.academics.closeout_rules import is_self_review as is_self_review
from app.modules.academics.closeout_rules import on_behalf_block as on_behalf_block
from app.modules.academics.closeout_rules import review_block as review_block
from app.modules.academics.closeout_rules import sees_summary as sees_summary
from app.modules.academics.closeout_rules import summary_errors as summary_errors
from app.modules.academics.closeouts import (
    approved_summary_columns as approved_summary_columns,
)
from app.modules.academics.closeouts import (
    closeout_for_session as closeout_for_session,
)
from app.modules.academics.closeouts import (
    closeout_session_keys as closeout_session_keys,
)
from app.modules.academics.closeouts import (
    closeout_sessions_of as closeout_sessions_of,
)
from app.modules.academics.closeouts import closeouts_by_id as closeouts_by_id
from app.modules.academics.closeouts import (
    create_session_closeout as create_session_closeout,
)
from app.modules.academics.closeouts import summary_of as summary_of
from app.modules.academics.errors import ActivePlanExistsError as ActivePlanExistsError
from app.modules.academics.errors import (
    AssignmentCreatorOnlyError as AssignmentCreatorOnlyError,
)
from app.modules.academics.errors import AssignmentFieldError as AssignmentFieldError
from app.modules.academics.errors import (
    AssignmentHasGradesError as AssignmentHasGradesError,
)
from app.modules.academics.errors import (
    AssignmentHasSubmissionsError as AssignmentHasSubmissionsError,
)
from app.modules.academics.errors import (
    AssignmentReadOnlyError as AssignmentReadOnlyError,
)
from app.modules.academics.errors import (
    AssignmentTutorOnlyError as AssignmentTutorOnlyError,
)
from app.modules.academics.errors import (
    CloseoutAlreadySubmittedError as CloseoutAlreadySubmittedError,
)
from app.modules.academics.errors import (
    CloseoutNotSubmittedError as CloseoutNotSubmittedError,
)
from app.modules.academics.errors import (
    CloseoutNotWithTutorError as CloseoutNotWithTutorError,
)
from app.modules.academics.errors import (
    CloseoutOnBehalfOwnSessionError as CloseoutOnBehalfOwnSessionError,
)
from app.modules.academics.errors import (
    CloseoutReviewRouteRequiredError as CloseoutReviewRouteRequiredError,
)
from app.modules.academics.errors import (
    CloseoutSelfReviewError as CloseoutSelfReviewError,
)
from app.modules.academics.errors import (
    CloseoutSubmittedOnBehalfError as CloseoutSubmittedOnBehalfError,
)
from app.modules.academics.errors import (
    CloseoutSummaryInvalidError as CloseoutSummaryInvalidError,
)
from app.modules.academics.errors import (
    CloseoutTutorOnlyError as CloseoutTutorOnlyError,
)
from app.modules.academics.errors import (
    CloseoutUnchangedError as CloseoutUnchangedError,
)
from app.modules.academics.errors import (
    CloseoutUnderReviewError as CloseoutUnderReviewError,
)
from app.modules.academics.errors import (
    CloseoutWithTutorError as CloseoutWithTutorError,
)
from app.modules.academics.errors import (
    EvaluationAuthorOnlyError as EvaluationAuthorOnlyError,
)
from app.modules.academics.errors import (
    EvaluationFieldError as EvaluationFieldError,
)
from app.modules.academics.errors import (
    EvaluationFinalError as EvaluationFinalError,
)
from app.modules.academics.errors import (
    EvaluationIncompleteError as EvaluationIncompleteError,
)
from app.modules.academics.errors import (
    EvaluationNotDraftError as EvaluationNotDraftError,
)
from app.modules.academics.errors import (
    EvaluationNotFinalError as EvaluationNotFinalError,
)
from app.modules.academics.errors import (
    EvaluationReadOnlyError as EvaluationReadOnlyError,
)
from app.modules.academics.errors import (
    EvaluationTemplateInactiveError as EvaluationTemplateInactiveError,
)
from app.modules.academics.errors import (
    EvaluationTemplateNameTakenError as EvaluationTemplateNameTakenError,
)
from app.modules.academics.errors import (
    EvaluationTutorOnlyError as EvaluationTutorOnlyError,
)
from app.modules.academics.errors import (
    EvaluationVoidedError as EvaluationVoidedError,
)
from app.modules.academics.errors import (
    LearningPlanFieldError as LearningPlanFieldError,
)
from app.modules.academics.errors import (
    NoteAlreadyPublishedError as NoteAlreadyPublishedError,
)
from app.modules.academics.errors import (
    NoteAuthorOnlyError as NoteAuthorOnlyError,
)
from app.modules.academics.errors import NoteFieldError as NoteFieldError
from app.modules.academics.errors import (
    NoteNotPublishedError as NoteNotPublishedError,
)
from app.modules.academics.errors import (
    NotePublishedError as NotePublishedError,
)
from app.modules.academics.errors import (
    NoteReadOnlyError as NoteReadOnlyError,
)
from app.modules.academics.errors import (
    NoteTutorOnlyError as NoteTutorOnlyError,
)
from app.modules.academics.errors import PlanClosedError as PlanClosedError
from app.modules.academics.errors import PlanNotActiveError as PlanNotActiveError
from app.modules.academics.errors import PlanNotDraftError as PlanNotDraftError
from app.modules.academics.errors import PlanReadOnlyError as PlanReadOnlyError
from app.modules.academics.errors import (
    PlanSubjectNotAssignedError as PlanSubjectNotAssignedError,
)
from app.modules.academics.errors import (
    ProgressReportAuthorNotCurrentError as ProgressReportAuthorNotCurrentError,
)
from app.modules.academics.errors import (
    ProgressReportAuthorOnlyError as ProgressReportAuthorOnlyError,
)
from app.modules.academics.errors import (
    ProgressReportClosedError as ProgressReportClosedError,
)
from app.modules.academics.errors import (
    ProgressReportExistsError as ProgressReportExistsError,
)
from app.modules.academics.errors import (
    ProgressReportFieldError as ProgressReportFieldError,
)
from app.modules.academics.errors import (
    ProgressReportNotSubmittedError as ProgressReportNotSubmittedError,
)
from app.modules.academics.errors import (
    ProgressReportReadOnlyError as ProgressReportReadOnlyError,
)
from app.modules.academics.errors import (
    ProgressReportReviewRouteRequiredError as ProgressReportReviewRouteRequiredError,
)
from app.modules.academics.errors import (
    ProgressReportScheduleExistsError as ProgressReportScheduleExistsError,
)
from app.modules.academics.errors import (
    ProgressReportScheduleFieldError as ProgressReportScheduleFieldError,
)
from app.modules.academics.errors import (
    ProgressReportSelfReviewError as ProgressReportSelfReviewError,
)
from app.modules.academics.errors import (
    ProgressReportSummaryRequiredError as ProgressReportSummaryRequiredError,
)
from app.modules.academics.errors import (
    ProgressReportTutorOnlyError as ProgressReportTutorOnlyError,
)
from app.modules.academics.errors import (
    ProgressReportUnderReviewError as ProgressReportUnderReviewError,
)
from app.modules.academics.errors import (
    RecipientHasSubmissionError as RecipientHasSubmissionError,
)
from app.modules.academics.errors import (
    ReportSubjectNotAssignedError as ReportSubjectNotAssignedError,
)
from app.modules.academics.errors import (
    ScheduleAuthorInvalidError as ScheduleAuthorInvalidError,
)
from app.modules.academics.errors import (
    ScheduleStudentInactiveError as ScheduleStudentInactiveError,
)
from app.modules.academics.errors import (
    ScheduleSubjectNotAssignedError as ScheduleSubjectNotAssignedError,
)
from app.modules.academics.errors import (
    ScheduleSubjectUnknownError as ScheduleSubjectUnknownError,
)
from app.modules.academics.errors import (
    SessionNotAssignableError as SessionNotAssignableError,
)
from app.modules.academics.errors import (
    SessionNotClosableError as SessionNotClosableError,
)
from app.modules.academics.errors import (
    SessionNotEndedError as SessionNotEndedError,
)
from app.modules.academics.errors import (
    SessionNotEvaluableError as SessionNotEvaluableError,
)
from app.modules.academics.errors import (
    SessionNotNotableError as SessionNotNotableError,
)
from app.modules.academics.errors import (
    SessionNotStartedError as SessionNotStartedError,
)
from app.modules.academics.errors import SubmissionGradedError as SubmissionGradedError
from app.modules.academics.errors import SubmissionLockedError as SubmissionLockedError
from app.modules.academics.errors import (
    SubmissionRequiredError as SubmissionRequiredError,
)
from app.modules.academics.errors import (
    SubmissionStudentOnlyError as SubmissionStudentOnlyError,
)
from app.modules.academics.evaluation_templates import (
    CriterionValues as CriterionValues,
)
from app.modules.academics.evaluation_templates import TemplateRow as TemplateRow
from app.modules.academics.evaluation_templates import (
    create_template as create_template,
)
from app.modules.academics.evaluation_templates import (
    list_templates as list_templates,
)
from app.modules.academics.evaluation_templates import (
    replace_template as replace_template,
)
from app.modules.academics.evaluation_templates import (
    template_with_criteria as template_with_criteria,
)
from app.modules.academics.evaluations import (
    CohortAssessmentEvaluation as CohortAssessmentEvaluation,
)
from app.modules.academics.evaluations import CopiedCriterion as CopiedCriterion
from app.modules.academics.evaluations import ScoreInput as ScoreInput
from app.modules.academics.evaluations import WorkingPage as WorkingPage
from app.modules.academics.evaluations import WorkingRow as WorkingRow
from app.modules.academics.evaluations import (
    change_evaluation as change_evaluation,
)
from app.modules.academics.evaluations import (
    cohort_assessment_evaluations as cohort_assessment_evaluations,
)
from app.modules.academics.evaluations import (
    create_evaluation as create_evaluation,
)
from app.modules.academics.evaluations import (
    evaluation_by_id as evaluation_by_id,
)
from app.modules.academics.evaluations import (
    evaluation_for_update as evaluation_for_update,
)
from app.modules.academics.evaluations import (
    finalize_evaluation as finalize_evaluation,
)
from app.modules.academics.evaluations import (
    has_own_evaluation as has_own_evaluation,
)
from app.modules.academics.evaluations import history_rows as history_rows
from app.modules.academics.evaluations import scores_of as scores_of
from app.modules.academics.evaluations import (
    soft_delete_evaluation as soft_delete_evaluation,
)
from app.modules.academics.evaluations import (
    void_evaluation as void_evaluation,
)
from app.modules.academics.evaluations import working_page as working_page
from app.modules.academics.learning_plans import MilestoneFact as MilestoneFact
from app.modules.academics.learning_plans import MilestoneValues as MilestoneValues
from app.modules.academics.learning_plans import ObjectiveRow as ObjectiveRow
from app.modules.academics.learning_plans import ObjectiveValues as ObjectiveValues
from app.modules.academics.learning_plans import PlanRow as PlanRow
from app.modules.academics.learning_plans import create_plan as create_plan
from app.modules.academics.learning_plans import (
    family_visible_condition as family_visible_condition,
)
from app.modules.academics.learning_plans import (
    get_milestone_for_update as get_milestone_for_update,
)
from app.modules.academics.learning_plans import (
    get_plan_for_share as get_plan_for_share,
)
from app.modules.academics.learning_plans import (
    get_plan_for_update as get_plan_for_update,
)
from app.modules.academics.learning_plans import has_own_plan as has_own_plan
from app.modules.academics.learning_plans import list_plans as list_plans
from app.modules.academics.learning_plans import milestone_facts as milestone_facts
from app.modules.academics.learning_plans import milestone_plan_id as milestone_plan_id
from app.modules.academics.learning_plans import (
    other_active_plan_exists as other_active_plan_exists,
)
from app.modules.academics.learning_plans import plan_items as plan_items
from app.modules.academics.learning_plans import plan_with_subject as plan_with_subject
from app.modules.academics.learning_plans import (
    replace_plan_content as replace_plan_content,
)
from app.modules.academics.learning_plans import (
    set_milestone_status as set_milestone_status,
)
from app.modules.academics.learning_plans import soft_delete_plan as soft_delete_plan
from app.modules.academics.learning_plans import transition_plan as transition_plan
from app.modules.academics.models import (
    CLOSEOUT_SUBMITTED as CLOSEOUT_SUBMITTED,
)
from app.modules.academics.models import Assignment as Assignment
from app.modules.academics.models import AssignmentSubmission as AssignmentSubmission
from app.modules.academics.models import Evaluation as Evaluation
from app.modules.academics.models import EvaluationScore as EvaluationScore
from app.modules.academics.models import (
    EvaluationTemplate as EvaluationTemplate,
)
from app.modules.academics.models import (
    EvaluationTemplateCriterion as EvaluationTemplateCriterion,
)
from app.modules.academics.models import LearningPlan as LearningPlan
from app.modules.academics.models import LearningPlanMilestone as LearningPlanMilestone
from app.modules.academics.models import LearningPlanObjective as LearningPlanObjective
from app.modules.academics.models import ProgressReport as ProgressReport
from app.modules.academics.models import (
    ProgressReportSchedule as ProgressReportSchedule,
)
from app.modules.academics.models import SessionCloseout as SessionCloseout
from app.modules.academics.models import (
    SessionCloseoutApproval as SessionCloseoutApproval,
)
from app.modules.academics.models import (
    SessionCloseoutCorrection as SessionCloseoutCorrection,
)
from app.modules.academics.models import SessionNote as SessionNote
from app.modules.academics.notes import NotePage as NotePage
from app.modules.academics.notes import NoteRow as NoteRow
from app.modules.academics.notes import create_note as create_note
from app.modules.academics.notes import (
    family_note_count_columns as family_note_count_columns,
)
from app.modules.academics.notes import (
    family_notes_for_student as family_notes_for_student,
)
from app.modules.academics.notes import (
    get_note_for_update as get_note_for_update,
)
from app.modules.academics.notes import note_by_id as note_by_id
from app.modules.academics.notes import (
    notes_for_session as notes_for_session,
)
from app.modules.academics.notes import (
    notes_for_student as notes_for_student,
)
from app.modules.academics.notes import publish_note as publish_note
from app.modules.academics.notes import (
    soft_delete_note as soft_delete_note,
)
from app.modules.academics.notes import (
    unpublish_note as unpublish_note,
)
from app.modules.academics.notes import (
    update_note_body as update_note_body,
)
from app.modules.academics.progress_aggregates import (
    HomeworkRow as HomeworkRow,
)
from app.modules.academics.progress_aggregates import (
    PlanCounts as PlanCounts,
)
from app.modules.academics.progress_aggregates import (
    ScoreRow as ScoreRow,
)
from app.modules.academics.progress_aggregates import (
    dashboard_plans as dashboard_plans,
)
from app.modules.academics.progress_aggregates import (
    evaluation_scores as evaluation_scores,
)
from app.modules.academics.progress_aggregates import (
    homework_rows as homework_rows,
)
from app.modules.academics.progress_aggregates import (
    homework_state_counts as homework_state_counts,
)
from app.modules.academics.progress_aggregates import (
    is_counted_grade as is_counted_grade,
)
from app.modules.academics.progress_aggregates import (
    plan_counts as plan_counts,
)
from app.modules.academics.progress_report_facts import (
    academic_facts as academic_facts,
)
from app.modules.academics.progress_report_facts import (
    facts_document as facts_document,
)
from app.modules.academics.progress_report_schedules import (
    PeriodKey as PeriodKey,
)
from app.modules.academics.progress_report_schedules import (
    PeriodReportRow as PeriodReportRow,
)
from app.modules.academics.progress_report_schedules import (
    create_schedule as create_schedule,
)
from app.modules.academics.progress_report_schedules import (
    deactivate_schedule as deactivate_schedule,
)
from app.modules.academics.progress_report_schedules import (
    due_schedule_ids as due_schedule_ids,
)
from app.modules.academics.progress_report_schedules import (
    get_schedule_for_update as get_schedule_for_update,
)
from app.modules.academics.progress_report_schedules import (
    insert_placeholder as insert_placeholder,
)
from app.modules.academics.progress_report_schedules import (
    period_reports as period_reports,
)
from app.modules.academics.progress_report_schedules import (
    reactivate_schedule as reactivate_schedule,
)
from app.modules.academics.progress_report_schedules import (
    schedules_for_student as schedules_for_student,
)
from app.modules.academics.progress_report_schedules import (
    split_period_reports as split_period_reports,
)
from app.modules.academics.progress_reports import (
    ReportPage as ReportPage,
)
from app.modules.academics.progress_reports import (
    ReportRow as ReportRow,
)
from app.modules.academics.progress_reports import (
    approve_report as approve_report,
)
from app.modules.academics.progress_reports import (
    approved_page as approved_page,
)
from app.modules.academics.progress_reports import (
    approved_report as approved_report,
)
from app.modules.academics.progress_reports import (
    change_narrative as change_narrative,
)
from app.modules.academics.progress_reports import (
    create_report as create_report,
)
from app.modules.academics.progress_reports import (
    get_report_for_update as get_report_for_update,
)
from app.modules.academics.progress_reports import (
    has_own_approved_report as has_own_approved_report,
)
from app.modules.academics.progress_reports import (
    progress_report_working_page as progress_report_working_page,
)
from app.modules.academics.progress_reports import (
    report_with_subject as report_with_subject,
)
from app.modules.academics.progress_reports import (
    reports_by_id as reports_by_id,
)
from app.modules.academics.progress_reports import (
    request_report_changes as request_report_changes,
)
from app.modules.academics.progress_reports import (
    retract_report as retract_report,
)
from app.modules.academics.progress_reports import (
    soft_delete_report as soft_delete_report,
)
from app.modules.academics.progress_reports import (
    submit_report as submit_report,
)
from app.modules.academics.schemas import CloseoutApprove as CloseoutApprove
from app.modules.academics.schemas import CloseoutCorrection as CloseoutCorrection
from app.modules.academics.schemas import (
    CloseoutOnBehalfSubmit as CloseoutOnBehalfSubmit,
)
from app.modules.academics.schemas import (
    CloseoutRequestChanges as CloseoutRequestChanges,
)
from app.modules.academics.schemas import CloseoutResubmit as CloseoutResubmit
from app.modules.academics.schemas import CloseoutTakeOver as CloseoutTakeOver
