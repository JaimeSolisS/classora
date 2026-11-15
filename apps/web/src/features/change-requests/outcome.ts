import type { ChangeRequestKind, SchedulingPolicy } from './api';

// The outcome preview of a change request (#62b A3): an estimate, shown
// before sending. It mirrors `classify_change` of
// apps/api/app/modules/administration/scheduling_policy.py for a
// cancellation and a reschedule; the server classifies the request when it
// is made and that stored outcome is the one that counts.
//
// - A tutor's request is excused, whatever the time.
// - A family's is on time when made at least `change_notice_hours` before
//   the start (exactly the notice is on time), late after that, and a
//   no-show from the start on (never offered here: A1 hides started
//   sessions).
// - A reschedule never allows a makeup; a cancellation follows
//   `makeup_after_on_time` / `makeup_after_late` / `makeup_after_no_show`,
//   and an excused one always allows it, until the start plus
//   `makeup_window_days` days of 24 hours.

export type PreviewClassification = 'on_time' | 'late' | 'no_show' | 'excused';

export interface OutcomePreview {
  classification: PreviewClassification;
  makeupAllowed: boolean;
  /** The last instant of the makeup window (ISO, UTC), or null. */
  makeupUntil: string | null;
}

export interface PreviewInput {
  kind: ChangeRequestKind;
  initiatedBy: 'family' | 'tutor';
  /** The session's start (an instant). */
  startsAt: string;
  /** When the request would be made (epoch milliseconds). */
  now: number;
}

const HOUR_MS = 3_600_000;
const DAY_MS = 24 * HOUR_MS;

type PolicyRules = Pick<
  SchedulingPolicy,
  | 'change_notice_hours'
  | 'makeup_after_on_time'
  | 'makeup_after_late'
  | 'makeup_after_no_show'
  | 'makeup_window_days'
>;

function classify(
  policy: PolicyRules,
  { initiatedBy, startsAt, now }: PreviewInput,
): PreviewClassification {
  if (initiatedBy === 'tutor') {
    return 'excused';
  }
  const start = Date.parse(startsAt);
  if (now >= start) {
    return 'no_show';
  }
  if (now > start - policy.change_notice_hours * HOUR_MS) {
    return 'late';
  }
  return 'on_time';
}

export function previewOutcome(
  policy: PolicyRules,
  input: PreviewInput,
): OutcomePreview {
  const classification = classify(policy, input);
  let allowed: boolean;
  if (input.kind === 'reschedule') {
    allowed = false;
  } else if (classification === 'excused') {
    allowed = true;
  } else if (classification === 'on_time') {
    allowed = policy.makeup_after_on_time;
  } else if (classification === 'late') {
    allowed = policy.makeup_after_late;
  } else {
    allowed = policy.makeup_after_no_show;
  }
  return {
    classification,
    makeupAllowed: allowed,
    makeupUntil: allowed
      ? new Date(
          Date.parse(input.startsAt) + policy.makeup_window_days * DAY_MS,
        ).toISOString()
      : null,
  };
}

export type PreviewTextKey =
  | 'changeRequests.preview.onTimeMakeup'
  | 'changeRequests.preview.onTimeNoMakeup'
  | 'changeRequests.preview.onTimeReschedule'
  | 'changeRequests.preview.lateMakeup'
  | 'changeRequests.preview.lateNoMakeup'
  | 'changeRequests.preview.lateReschedule'
  | 'changeRequests.preview.excusedMakeup'
  | 'changeRequests.preview.excused';

/** The message key of a preview; null for a no-show (never shown, A1). */
export function previewTextKey(
  kind: ChangeRequestKind,
  preview: OutcomePreview,
): PreviewTextKey | null {
  switch (preview.classification) {
    case 'excused':
      return preview.makeupAllowed
        ? 'changeRequests.preview.excusedMakeup'
        : 'changeRequests.preview.excused';
    case 'on_time':
      if (kind === 'reschedule') {
        return 'changeRequests.preview.onTimeReschedule';
      }
      return preview.makeupAllowed
        ? 'changeRequests.preview.onTimeMakeup'
        : 'changeRequests.preview.onTimeNoMakeup';
    case 'late':
      if (kind === 'reschedule') {
        return 'changeRequests.preview.lateReschedule';
      }
      return preview.makeupAllowed
        ? 'changeRequests.preview.lateMakeup'
        : 'changeRequests.preview.lateNoMakeup';
    default:
      return null;
  }
}
