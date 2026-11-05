import type { TFunction } from 'i18next';
import { roleNameKey } from '@/features/users/roles';
import {
  affectedSessionText,
  describeChange,
  moreCount,
} from '@/features/tutors/changeRequests';
import type { Formatters } from '@/lib/formatters';
import type { ApprovalRequestItem } from './api';
import {
  availabilityChangeSubject,
  itemReviewLink,
  progressReportReviewLink,
  sessionCloseoutLink,
  changeRequestSubject,
  occurrenceCodeName,
  occurrenceConflictSubject,
  payrollPeriodSubject,
  progressReportSubject,
  qualificationSubject,
  reassignmentSubject,
  registrationSubject,
  sessionCloseoutSubject,
  sessionRequestSubject,
} from './summary';

// The detail panel's per-type fields (M8-01b B2): one renderer per known
// approval type, listing the display fields of the type's `subject` with
// labels, reusing the summary's subject parsers. Ids are not listed: they
// back the panel's review links. An empty or null optional field is left
// out. A type without a renderer, or a subject its renderer can't read,
// shows its code and the raw keys (`rawDetails`), so it is never hidden.

/** One labelled field; a list value shows as a list. */
export interface DetailRow {
  label: string;
  value: string | string[];
}

type Renderer = (
  item: ApprovalRequestItem,
  t: TFunction,
  formatters: Formatters,
) => DetailRow[] | null;

/** `t(prefix.code)`, or the code itself when the catalog has no such key
 * (a value added by a later API). */
function codeText(t: TFunction, prefix: string, code: string): string {
  return (t as unknown as (key: string, options: object) => string)(
    `${prefix}.${code}`,
    { defaultValue: code },
  );
}

/** The rows whose value is not empty. */
function present(rows: (DetailRow | null | false)[]): DetailRow[] {
  return rows.filter(
    (row): row is DetailRow =>
      Boolean(row) &&
      (Array.isArray((row as DetailRow).value)
        ? (row as DetailRow).value.length > 0
        : (row as DetailRow).value !== ''),
  );
}

function row(label: string, value: string | null | undefined): DetailRow {
  return { label, value: value ?? '' };
}

function minutes(t: TFunction, value: number | null | undefined): string {
  return typeof value === 'number'
    ? t('approvals.details.minutesValue', { minutes: value })
    : '';
}

const registration: Renderer = (item, t) => {
  const subject = registrationSubject(item);
  return subject
    ? present([
        row(t('approvals.details.displayName'), subject.display_name),
        row(t('approvals.details.username'), subject.username),
        row(
          t('approvals.details.requestedRole'),
          t(roleNameKey(subject.requested_role)),
        ),
      ])
    : null;
};

const qualification: Renderer = (item, t) => {
  const subject = qualificationSubject(item);
  return subject
    ? present([
        row(t('approvals.details.tutor'), subject.tutor_display_name),
        row(t('approvals.details.subject'), subject.subject_name),
        row(t('approvals.details.level'), subject.education_level_name),
      ])
    : null;
};

const offerReassignment: Renderer = (item, t, f) => {
  const subject = reassignmentSubject(item);
  return subject
    ? present([
        row(t('approvals.details.tutor'), subject.tutor_name),
        row(
          t('approvals.details.offerOutcome'),
          codeText(t, 'approvals.details.offerOutcomes', subject.outcome),
        ),
        subject.kind
          ? row(
              t('approvals.details.offerKind'),
              codeText(t, 'approvals.details.offerKinds', subject.kind),
            )
          : null,
        row(t('approvals.details.subject'), subject.subject_name),
        subject.starts_at
          ? row(
              t('approvals.details.when'),
              f.formatInstant(subject.starts_at, 'dateTime'),
            )
          : null,
        subject.from_date
          ? row(
              t('approvals.details.series'),
              t('approvals.details.seriesFrom', {
                date: f.formatLocalDate(subject.from_date),
              }),
            )
          : null,
        typeof subject.occurrence_count === 'number'
          ? row(
              t('approvals.details.occurrenceCount'),
              String(subject.occurrence_count),
            )
          : null,
        subject.session_state
          ? row(
              t('approvals.details.sessionState'),
              codeText(t, 'scheduling.states', subject.session_state),
            )
          : null,
        row(t('approvals.details.originalTutor'), subject.original_tutor_name),
        row(t('approvals.details.declineNote'), subject.decline_note),
      ])
    : null;
};

const occurrenceConflict: Renderer = (item, t, f) => {
  const subject = occurrenceConflictSubject(item);
  return subject
    ? present([
        row(
          t('approvals.details.when'),
          f.formatInstant(subject.starts_at, 'dateTime'),
        ),
        row(t('approvals.details.subject'), subject.subject_name),
        {
          label: t('approvals.details.students'),
          value: subject.student_display_names,
        },
        row(t('approvals.details.tutor'), subject.tutor_display_name),
        {
          label: t('approvals.details.conflicts'),
          value: subject.conflict_codes.map((code) =>
            occurrenceCodeName(code, t),
          ),
        },
      ])
    : null;
};

const sessionRequest: Renderer = (item, t, f) => {
  const subject = sessionRequestSubject(item);
  if (!subject) {
    return null;
  }
  const status = item.subject.status;
  return present([
    row(t('approvals.details.student'), subject.student_name),
    row(t('approvals.details.subject'), subject.subject_name),
    row(t('approvals.details.level'), subject.education_level_name),
    row(
      t('approvals.details.when'),
      f.formatInstant(subject.starts_at, 'dateTime'),
    ),
    row(
      t('approvals.details.endsAt'),
      f.formatInstant(subject.ends_at, 'time'),
    ),
    row(t('approvals.details.duration'), minutes(t, subject.duration_minutes)),
    row(t('approvals.details.requestedBy'), subject.requested_by),
    row(t('approvals.details.note'), subject.note),
    typeof status === 'string'
      ? row(
          t('approvals.details.requestStatus'),
          codeText(t, 'sessionRequests.status', status),
        )
      : null,
  ]);
};

const changeRequest: Renderer = (item, t, f) => {
  const subject = changeRequestSubject(item);
  if (!subject) {
    return null;
  }
  const level = item.subject.education_level_name;
  return present([
    row(
      t('approvals.details.changeKind'),
      codeText(t, 'scheduling.changeRequest.kinds', subject.kind),
    ),
    row(
      t('approvals.details.initiatedBy'),
      codeText(t, 'approvals.details.initiators', subject.initiated_by),
    ),
    row(t('approvals.details.student'), subject.student_name),
    row(t('approvals.details.subject'), subject.subject_name),
    typeof level === 'string' ? row(t('approvals.details.level'), level) : null,
    row(
      t('approvals.details.when'),
      f.formatInstant(subject.starts_at, 'dateTime'),
    ),
    subject.proposed_starts_at
      ? row(
          t('approvals.details.proposedStart'),
          f.formatInstant(subject.proposed_starts_at, 'dateTime'),
        )
      : null,
    row(
      t('approvals.details.classification'),
      codeText(
        t,
        'scheduling.changeRequest.classification',
        subject.classification,
      ),
    ),
    row(
      t('approvals.details.requestStatus'),
      codeText(t, 'scheduling.changeRequest.statuses', subject.status),
    ),
  ]);
};

const availabilityChange: Renderer = (item, t, f) => {
  const subject = availabilityChangeSubject(item);
  if (!subject) {
    return null;
  }
  const more = moreCount(
    subject.affected_count,
    subject.affected_sessions.length,
  );
  return present([
    row(t('approvals.details.tutor'), subject.tutor.display_name),
    row(
      t('approvals.details.change'),
      describeChange(subject, t, (date) => f.formatLocalDate(date)),
    ),
    row(t('approvals.details.affectedCount'), String(subject.affected_count)),
    {
      label: t('approvals.availabilityChange.affectedTitle'),
      value: [
        ...subject.affected_sessions.map((session) =>
          affectedSessionText(session, t, f.formatInstant),
        ),
        ...(more > 0
          ? [t('approvals.availabilityChange.more', { count: more })]
          : []),
      ],
    },
    row(
      t('approvals.details.requestStatus'),
      codeText(
        t,
        'approvals.details.changeStatuses',
        subject.change_request_status,
      ),
    ),
  ]);
};

const sessionCloseout: Renderer = (item, t, f) => {
  const subject = sessionCloseoutSubject(item);
  return subject
    ? present([
        subject.kind_of_review
          ? row(
              t('approvals.details.reviewKind'),
              t(`approvals.closeout.kinds.${subject.kind_of_review}`),
            )
          : null,
        row(t('approvals.details.subject'), subject.subject_name),
        row(
          t('approvals.details.when'),
          f.formatInstant(subject.starts_at, 'dateTime'),
        ),
        row(t('approvals.details.tutor'), subject.tutor_display_name),
        row(
          t('approvals.details.sessionKind'),
          codeText(t, 'scheduling.calendar.filters.kinds', subject.kind),
        ),
        row(
          t('approvals.details.scheduledMinutes'),
          minutes(t, subject.scheduled_minutes),
        ),
        row(
          t('approvals.details.actualMinutes'),
          minutes(t, subject.actual_minutes),
        ),
        row(
          t('approvals.details.participants'),
          String(subject.participant_count),
        ),
        row(
          t('approvals.details.approvedMinutes'),
          minutes(t, subject.approved_minutes),
        ),
        row(
          t('approvals.details.onBehalfBy'),
          subject.on_behalf_by_display_name,
        ),
      ])
    : null;
};

const progressReport: Renderer = (item, t, f) => {
  const subject = progressReportSubject(item);
  return subject
    ? present([
        row(t('approvals.details.student'), subject.student.display_name),
        row(
          t('approvals.details.reportKind'),
          codeText(t, 'progressReports.kinds', subject.kind),
        ),
        row(
          t('approvals.details.period'),
          t('approvals.details.periodValue', {
            start: f.formatLocalDate(subject.period_start),
            end: f.formatLocalDate(subject.period_end),
          }),
        ),
        row(
          t('approvals.details.subject'),
          subject.subject?.name ?? t('approvals.details.allSubjects'),
        ),
        row(t('approvals.details.author'), subject.author.display_name),
        subject.submitted_at
          ? row(
              t('approvals.details.submittedAt'),
              f.formatInstant(subject.submitted_at, 'dateTime'),
            )
          : null,
      ])
    : null;
};

const payrollPeriod: Renderer = (item, t, f) => {
  const subject = payrollPeriodSubject(item);
  if (!subject) {
    return null;
  }
  const preparedAt = item.subject.prepared_at;
  return present([
    row(t('approvals.details.tutor'), subject.tutor.display_name),
    row(
      t('approvals.details.period'),
      t('approvals.details.periodValue', {
        start: f.formatLocalDate(subject.starts_on),
        end: f.formatLocalDate(subject.ends_on),
      }),
    ),
    row(t('approvals.details.preparedBy'), subject.prepared_by?.display_name),
    typeof preparedAt === 'string' && preparedAt
      ? row(
          t('approvals.details.preparedAt'),
          f.formatInstant(preparedAt, 'dateTime'),
        )
      : null,
  ]);
};

/** The panel renderer of each known type (E1's pin: the same codes as
 * `APPROVAL_TYPE_LABELS`). */
export const DETAIL_RENDERERS: Readonly<Record<string, Renderer>> = {
  'identity.registration': registration,
  'staff.qualification': qualification,
  'scheduling.offer_reassignment': offerReassignment,
  'scheduling.occurrence_conflict': occurrenceConflict,
  'scheduling.session_request': sessionRequest,
  'scheduling.change_request': changeRequest,
  'staff.availability_change': availabilityChange,
  'academics.session_closeout': sessionCloseout,
  'academics.progress_report': progressReport,
  'payroll.period': payrollPeriod,
};

function rawValue(value: unknown): string {
  if (typeof value === 'string') {
    return value;
  }
  if (typeof value === 'number' || typeof value === 'boolean') {
    return String(value);
  }
  return value === null || value === undefined
    ? ''
    : (JSON.stringify(value) ?? '');
}

const OPEN_STATUSES = new Set(['pending', 'changes_requested']);

/** Whether the request is still open (pending or changes requested): only
 * then it takes an assignment (#118 A2). */
export function isOpenItem(item: ApprovalRequestItem): boolean {
  return OPEN_STATUSES.has(item.status);
}

/** The review page of an item decided on its own page (#60, #62, #77,
 * #110b, #315), or null. */
export function ownPageLink(item: ApprovalRequestItem): string | null {
  return (
    itemReviewLink(item) ??
    progressReportReviewLink(item)?.to ??
    sessionCloseoutLink(item)
  );
}

/** The subject's raw keys with their values (the unknown-type rule). */
export function rawDetails(item: ApprovalRequestItem): DetailRow[] {
  return Object.entries(item.subject).map(([key, value]) => ({
    label: key,
    value: rawValue(value),
  }));
}

/**
 * The panel's fields of `item`: the type's renderer (`known: true`), or the
 * raw keys when the type has none or its subject can't be read.
 */
export function approvalDetails(
  item: ApprovalRequestItem,
  t: TFunction,
  formatters: Formatters,
): { known: boolean; rows: DetailRow[] } {
  const rows = DETAIL_RENDERERS[item.request_type]?.(item, t, formatters);
  return rows
    ? { known: true, rows }
    : { known: false, rows: rawDetails(item) };
}
