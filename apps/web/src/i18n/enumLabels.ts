import { widgetOf } from '@/features/dashboard/widgets';

// #130 C2 (E3): every enum of the API contract (packages/api-client/
// openapi.json) is classified here, in exactly one of the two maps:
//
// - ENUM_LABELS: the key builder of its values' labels: the feature's own
//   builder, or the template the UI uses (`common` keys; `errors:` for an
//   errors key). Numeric values arrive as strings.
// - UNLABELLED_ENUMS: why its values are never shown as labels.
//
// An enum id is a named component schema with `enum`, or
// `<Schema>.<property>` for an inline enum on a component property.
// enumLabels.test.ts fails on a new enum until it is classified, on a stale
// entry, and on a value with no es or en label.

/** The label key of one enum value. */
export type EnumLabelKey = (value: string) => string;

/** The `<prefix>.<value>` builder; a dot in a value becomes `_`, as the UI
 * does for codes such as `payroll.finalize`. */
function under(prefix: string): EnumLabelKey {
  return (value) => `${prefix}.${value.replaceAll('.', '_')}`;
}

export const ENUM_LABELS: Readonly<Record<string, EnumLabelKey>> = {
  AffectedSessionState: under('mySchedule.states'),
  AllocationMode: under('billing.accounts.record.modes'),
  ApprovalAssigneeFilter: under('approvals.assignee'),
  ApprovalPriority: under('approvals.priority'),
  ApprovalSort: under('approvals.sort'),
  ApprovalStatus: under('approvals.status'),
  ApprovalStatusFilter: under('approvals.status'),
  AssessmentOutcome: under('courses.cohorts.completion.outcome'),
  AttendanceBlock: under('attendance.blocked'),
  AttendanceStatus: under('attendance.statuses'),
  AvailabilityReason: (v) =>
    v === 'center_closed'
      ? 'scheduling.conflicts.center_closed'
      : `scheduling.reasons.${v}`,
  BillableState: under('mySchedule.states'),
  BlockingCode: under('billing.withdrawals.blocking'),
  'CancellationRuleCreate.excused_outcome': under(
    'billing.settings.cancellation.outcomes',
  ),
  'CancellationRuleCreate.late_outcome': under(
    'billing.settings.cancellation.outcomes',
  ),
  'CancellationRuleCreate.no_show_outcome': under(
    'billing.settings.cancellation.outcomes',
  ),
  'CancellationRuleCreate.on_time_outcome': under(
    'billing.settings.cancellation.outcomes',
  ),
  'CancellationRuleItem.excused_outcome': under(
    'billing.settings.cancellation.outcomes',
  ),
  'CancellationRuleItem.late_outcome': under(
    'billing.settings.cancellation.outcomes',
  ),
  'CancellationRuleItem.no_show_outcome': under(
    'billing.settings.cancellation.outcomes',
  ),
  'CancellationRuleItem.on_time_outcome': under(
    'billing.settings.cancellation.outcomes',
  ),
  CatalogPricingModel: under('billing.prices.modelFilters'),
  CatalogStatus: under('exports.form.studentStatuses'),
  ChangeClassification: under('scheduling.changeRequest.classification'),
  ChangeKind: under('tutors.availability.changes.kinds'),
  ChangeRequestInitiator: under('scheduling.changeRequest.initiators'),
  ChangeRequestKind: under('scheduling.changeRequest.kinds'),
  ChangeRequestStatus: under('scheduling.changeRequest.statuses'),
  ChangeStatus: under('tutors.availability.changes.statuses'),
  ChargeStatus: under('billing.tuition.chargeStatus'),
  ClassKind: under('exports.form.kinds'),
  ClassType: under('exports.form.kinds'),
  CloseoutApprovalSource: under('closeout.review.history.sources'),
  CloseoutBlockCode: (v) =>
    v === 'closeout_already_submitted'
      ? `errors:${v}`
      : `closeout.blocked.${v}`,
  CloseoutCorrectedField: (v) =>
    v === 'actual_minutes' ? 'closeout.actualMinutes' : `closeout.fields.${v}`,
  ClosureType: under('catalog.closureTypes'),
  'CohortListItem.assignment_status': under('offers.status'),
  CohortStatus: under('courses.cohorts.status'),
  CohortStatusFilter: under('courses.cohorts.list.statusOptions'),
  CompensationStatus: under('billing.prices.status'),
  CompletionStatus: under('courses.cohorts.completion.status'),
  ConsentMethod: under('consent.method'),
  ConsentScope: under('consent.scope'),
  CourseTemplateStatus: under('exports.form.studentStatuses'),
  CreditAllocationMode: under('billing.accounts.record.modes'),
  CreditPolicy: under('billing.withdrawals.policies'),
  CriteriaChargeKind: under('billing.discounts.chargeKinds'),
  CriteriaClassType: under('exports.form.kinds'),
  CriterionStatus: under('courses.cohorts.completion.criterion'),
  CustomPriceStatus: under('billing.prices.status'),
  DelegablePermissionCode: under('delegations'),
  DeletedRecordKind: under('recentlyDeleted.kinds'),
  DiscountAudience: under('billing.discounts.audience'),
  DiscountKind: under('billing.discounts.kinds'),
  DiscountStatus: under('billing.discounts.status'),
  Due: under('assignments.filters.dues'),
  EnrollmentChargeKind: under('billing.unbilled.enrollments.kinds'),
  EvaluationKind: under('academics.kinds'),
  EvaluationStatus: under('academics.history.working.statuses'),
  ExceptionKind: under('tutors.availability.kinds'),
  ExpenseStatus: under('finance.expenses.statuses'),
  ExportErrorCode: under('exports.history.failure'),
  ExportFormat: under('exports.formats'),
  ExportJobStatus: under('exports.status'),
  ExportLanguage: under('language.options'),
  'FamilyScheduleItem.attendance': under('attendance.statuses'),
  FamilyScheduleState: under('mySchedule.states'),
  FamilyStatus: under('exports.form.studentStatuses'),
  Frequency: under('finance.templates.frequencies'),
  GroupBy: under('academics.history.groups'),
  GuardianRelationship: under('relationships'),
  GuardianStatus: under('exports.form.studentStatuses'),
  ImportJobStatus: under('imports.status'),
  ImportRowStatus: under('imports.rowStatus'),
  'InvitationListItem.status': under('billing.packages.status'),
  JobStatus: under('jobs.status'),
  JobStatusFilter: under('jobs.statusFilter'),
  Language: under('language.options'),
  LedgerEntryType: under('billing.accounts.ledger.types'),
  LineKind: under('payroll.period.lines.kinds'),
  MarkerCode: under('scheduling.markers'),
  MilestoneStatus: under('learningPlans.milestoneStatus'),
  OfferKindCode: under('approvals.details.offerKinds'),
  OfferScopeCode: under('scheduling.offer.series'),
  OfferStatusCode: under('offers.status'),
  PackageStatus: under('billing.packages.status'),
  PackageTransactionKind: under('billing.packages.history.kinds'),
  PackageUnit: under('billing.packages.unitNames'),
  PaidState: under('payroll.periods.paidStates'),
  PayFrequency: under('billing.prices.frequencies'),
  PayItemDirection: under('payroll.payItems.directions'),
  PayItemPeriodStatus: under('payroll.payItems.lands.status'),
  PayItemRecurrence: under('payroll.payItems.recurrences'),
  PayRuleEvent: under('payroll.payRules.events'),
  PayRuleMode: under('payroll.payRules.sentences'),
  'PendingOffer.scope': under('scheduling.offer.series'),
  PeriodFrequency: under('billing.prices.frequencies'),
  PeriodState: (v) =>
    `billing.tuition.periodStates.${v === 'not_due' ? 'upcoming' : v}`,
  PeriodStatus: under('payroll.periods.statuses'),
  PeriodType: under('catalog.periodTypes'),
  PlanAction: under('learningPlans.actions'),
  PlanStatus: under('learningPlans.status'),
  'PreviewUnits.unit': under('billing.packages.unitNames'),
  PriceVersionStatus: under('billing.prices.status'),
  ProgressPlanStatus: under('progress.plans.statuses'),
  ProrationBasis: under('payroll.settings.bases'),
  QualificationStatus: under('tutors.qualifications.statuses'),
  RateModel: under('payroll.models'),
  RegistrationRole: under('register.role'),
  ReportBasis: under('reports.profitability.notes'),
  ReportClassType: under('reports.profitability.classTypes'),
  ReportKind: under('progressReports.kinds'),
  ReportStatus: under('progressReports.schedule.periodStatus'),
  RequirementStatus: under('courses.cohorts.completion.criterion'),
  RoleCode: under('roles'),
  ScheduleCadence: under('progressReports.schedule.cadenceLower'),
  ScheduleIneligibleReason: under('progressReports.schedule.ineligible'),
  SchedulePeriodStatus: under('progressReports.schedule.periodStatus'),
  'SchedulingConflict.severity': under('scheduling.severity'),
  SchedulingWarningCode: under('scheduling.warnings'),
  'SeriesAssignment.status': under('offers.status'),
  SeriesEditMode: under('scheduling.series.scope'),
  SessionKindCode: under('exports.form.kinds'),
  SessionLogKind: under('exports.form.kinds'),
  SessionLogMark: under('attendance.statuses'),
  SessionRequestDuration: () => 'sessionRequests.minutes',
  SessionRequestStatus: under('sessionRequests.status'),
  SessionStateCode: under('scheduling.states'),
  SharedRole: under('roles'),
  StudentStatus: under('exports.form.studentStatuses'),
  SubjectKind: under('billing.withdrawals.subjectKinds'),
  SubmissionState: under('assignments.states'),
  SubmittedVia: under('roles'),
  SystemKey: under('finance.categoryLabels'),
  TemplateStatus: under('finance.templates.statuses'),
  Theme: under('theme.options'),
  TuitionPlanStatus: under('billing.tuition.status'),
  TuitionUnbilledReason: under('billing.unbilled.tuition.reasons'),
  TutorOptionState: under('userStates'),
  TutorScheduleState: under('mySchedule.states'),
  UnbilledClassType: under('billing.tuition.kinds'),
  UnbilledReason: under('billing.unbilled.reasons'),
  UnresolvedReason: under('payroll.unresolved.reasons'),
  UnresolvedSource: under('payroll.unresolved.sources'),
  UserState: under('userStates'),
  WarningCode: under('billing.withdrawals.warnings'),
  WidgetCode: (v) => widgetOf(v)?.titleKey ?? `dashboard.widgets.${v}`,
  'WithdrawalDetail.outcome': under('billing.withdrawals.outcomes'),
  'WithdrawalItem.outcome': under('billing.withdrawals.outcomes'),
  'WithdrawalLine.kind': under('billing.discounts.chargeKinds'),
  'WithdrawalPreview.enrollment_status': under('billing.packages.status'),
  app__modules__billing__schemas__AdjustmentKind: under(
    'billing.accounts.adjustmentKinds',
  ),
  app__modules__payroll__schemas__AdjustmentKind: under(
    'payroll.period.lines.adjustmentKinds',
  ),
};

export const UNLABELLED_ENUMS: Readonly<Record<string, string>> = {
  ApprovalAction:
    'The action a decision button sends; each button has its own label.',
  ClassSessionsColumn:
    "An export column code: the API sends each column's label (GET /api/v1/exports/datasets, server exports catalog).",
  ClassSessionsGroupable:
    "An export column code: the API sends each column's label (GET /api/v1/exports/datasets, server exports catalog).",
  CloseoutStatus:
    'Chooses which closeout view a tutor sees (tutor-portal/closeout/rules.ts); never shown as a value.',
  ColumnKind: 'How an export writes a column; never shown.',
  DateFilter:
    'Whether an export dataset takes a date range (the form shows or hides it); never shown.',
  ExpensesColumn:
    "An export column code: the API sends each column's label (GET /api/v1/exports/datasets, server exports catalog).",
  ExpensesGroupable:
    "An export column code: the API sends each column's label (GET /api/v1/exports/datasets, server exports catalog).",
  FamilyRelation:
    "Whose item a family schedule row is (self or a guardian's student); the page shows the student's name.",
  ModuleEvidence:
    "Chooses the completion detail's sentence (StudentCompletionDetail); never shown as a value.",
  NoteVisibility:
    "A session note's audience; the notes sections say it in their own sentences.",
  PermissionCode:
    'Permission codes are never shown; the delegations page labels the four delegable ones (DelegablePermissionCode).',
  QualificationStatusFilter:
    'A query filter value; the qualifications page labels each status (QualificationStatus).',
  RevocationCause:
    "Chooses the consent section's revocation sentence (ConsentSection); never shown as a value.",
  ScheduleAccessCode:
    'A gate: the history page shows the consent message instead of the schedule.',
  StudentsColumn:
    "An export column code: the API sends each column's label (GET /api/v1/exports/datasets, server exports catalog).",
  StudentsGroupable:
    "An export column code: the API sends each column's label (GET /api/v1/exports/datasets, server exports catalog).",
  TutorField:
    'Names the tutor profile fields the caller may edit; never shown.',
  'WithdrawalLine.posting':
    "Chooses the settlement line's sentence (WithdrawalSettlement); never shown as a value.",
};
