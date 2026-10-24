import { describe, expect, it } from 'vitest';
import { POLICY } from './fixtures';
import {
  previewOutcome,
  previewTextKey,
  type PreviewClassification,
} from './outcome';

// #62b D4: previewOutcome against the boundary cases of the API's
// tests/unit/test_scheduling_policy.py (the same policy: 24 h change
// notice, a makeup after an on-time cancellation only, a 30-day window),
// written once as a table so the two evaluators can't drift silently.

const STARTS = '2026-11-10T16:00:00Z';
const H = 3_600_000;
const M = 60_000;
const at = (offset: number) => Date.parse(STARTS) + offset;
const MAKEUP_UNTIL = '2026-12-10T16:00:00.000Z';

interface Case {
  name: string;
  kind: 'cancellation' | 'reschedule';
  initiatedBy: 'family' | 'tutor';
  now: number;
  policy?: Partial<typeof POLICY>;
  classification: PreviewClassification;
  makeupUntil: string | null;
}

const CASES: Case[] = [
  {
    name: '25 h ahead: on time, with a makeup',
    kind: 'cancellation',
    initiatedBy: 'family',
    now: at(-25 * H),
    classification: 'on_time',
    makeupUntil: MAKEUP_UNTIL,
  },
  {
    name: '24 h and 1 min ahead: on time',
    kind: 'cancellation',
    initiatedBy: 'family',
    now: at(-24 * H - M),
    classification: 'on_time',
    makeupUntil: MAKEUP_UNTIL,
  },
  {
    name: 'exactly 24 h ahead: on time (the notice itself is on time)',
    kind: 'cancellation',
    initiatedBy: 'family',
    now: at(-24 * H),
    classification: 'on_time',
    makeupUntil: MAKEUP_UNTIL,
  },
  {
    name: '23 h 59 min ahead: late, no makeup',
    kind: 'cancellation',
    initiatedBy: 'family',
    now: at(-23 * H - 59 * M),
    classification: 'late',
    makeupUntil: null,
  },
  {
    name: '1 min ahead: late',
    kind: 'cancellation',
    initiatedBy: 'family',
    now: at(-M),
    classification: 'late',
    makeupUntil: null,
  },
  {
    name: 'at the start: a no-show',
    kind: 'cancellation',
    initiatedBy: 'family',
    now: at(0),
    classification: 'no_show',
    makeupUntil: null,
  },
  {
    name: 'a reschedule on time: never a makeup',
    kind: 'reschedule',
    initiatedBy: 'family',
    now: at(-25 * H),
    classification: 'on_time',
    makeupUntil: null,
  },
  {
    name: 'a reschedule, late, even when late cancellations allow one',
    kind: 'reschedule',
    initiatedBy: 'family',
    now: at(-H),
    policy: { makeup_after_late: true },
    classification: 'late',
    makeupUntil: null,
  },
  {
    name: 'a tutor cancellation: excused with a makeup, whatever the time',
    kind: 'cancellation',
    initiatedBy: 'tutor',
    now: at(-H),
    policy: { makeup_after_on_time: false },
    classification: 'excused',
    makeupUntil: MAKEUP_UNTIL,
  },
  {
    name: 'a tutor reschedule: excused, no makeup',
    kind: 'reschedule',
    initiatedBy: 'tutor',
    now: at(-25 * H),
    classification: 'excused',
    makeupUntil: null,
  },
  {
    name: 'makeup_after_late = true: a late cancellation allows a makeup',
    kind: 'cancellation',
    initiatedBy: 'family',
    now: at(-23 * H - 59 * M),
    policy: { makeup_after_late: true },
    classification: 'late',
    makeupUntil: MAKEUP_UNTIL,
  },
  {
    name: 'makeup_after_on_time = false: on time, no makeup',
    kind: 'cancellation',
    initiatedBy: 'family',
    now: at(-25 * H),
    policy: { makeup_after_on_time: false },
    classification: 'on_time',
    makeupUntil: null,
  },
  {
    name: 'a 12 h notice moves the boundary',
    kind: 'cancellation',
    initiatedBy: 'family',
    now: at(-12 * H),
    policy: { change_notice_hours: 12 },
    classification: 'on_time',
    makeupUntil: MAKEUP_UNTIL,
  },
  {
    name: 'no change notice: nothing before the start is late',
    kind: 'cancellation',
    initiatedBy: 'family',
    now: at(-1),
    policy: { change_notice_hours: 0 },
    classification: 'on_time',
    makeupUntil: MAKEUP_UNTIL,
  },
];

describe('previewOutcome (A3)', () => {
  it.each(CASES)('$name', (row) => {
    const preview = previewOutcome(
      { ...POLICY, ...row.policy },
      {
        kind: row.kind,
        initiatedBy: row.initiatedBy,
        startsAt: STARTS,
        now: row.now,
      },
    );
    expect(preview).toEqual({
      classification: row.classification,
      makeupAllowed: row.makeupUntil !== null,
      makeupUntil: row.makeupUntil,
    });
  });

  it('compares instants: the same start in another offset gives the same answer', () => {
    const cancun = '2026-11-10T11:00:00-05:00';
    expect(
      previewOutcome(POLICY, {
        kind: 'cancellation',
        initiatedBy: 'family',
        startsAt: cancun,
        now: at(-24 * H),
      }).classification,
    ).toBe('on_time');
  });

  it('counts the makeup window in days of 24 hours', () => {
    const preview = previewOutcome(
      { ...POLICY, makeup_window_days: 7 },
      {
        kind: 'cancellation',
        initiatedBy: 'family',
        startsAt: STARTS,
        now: at(-25 * H),
      },
    );
    expect(preview.makeupUntil).toBe('2026-11-17T16:00:00.000Z');
  });
});

describe('previewTextKey', () => {
  const preview = (
    classification: PreviewClassification,
    makeupAllowed: boolean,
  ) => ({ classification, makeupAllowed, makeupUntil: null });

  it.each([
    ['cancellation', 'on_time', true, 'changeRequests.preview.onTimeMakeup'],
    ['cancellation', 'on_time', false, 'changeRequests.preview.onTimeNoMakeup'],
    ['reschedule', 'on_time', false, 'changeRequests.preview.onTimeReschedule'],
    ['cancellation', 'late', true, 'changeRequests.preview.lateMakeup'],
    ['cancellation', 'late', false, 'changeRequests.preview.lateNoMakeup'],
    ['reschedule', 'late', false, 'changeRequests.preview.lateReschedule'],
    ['cancellation', 'excused', true, 'changeRequests.preview.excusedMakeup'],
    ['reschedule', 'excused', false, 'changeRequests.preview.excused'],
    ['cancellation', 'no_show', false, null],
  ] as const)('%s %s (makeup %s) → %s', (kind, classification, makeup, key) => {
    expect(previewTextKey(kind, preview(classification, makeup))).toBe(key);
  });
});
