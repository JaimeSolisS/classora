import { afterAll, beforeAll, describe, expect, it } from 'vitest';
import { ApiError } from '@/app/api';
import { todayInZone } from '@/lib/time';
import {
  APPROVED,
  APPROVED_REVIEW,
  CHANGES_REQUESTED,
  DRAFT,
  FORMER_AUTHOR,
  OWN_SUBMITTED_REVIEWER,
  RETRACTED,
  REVIEW,
  SUBMITTED,
} from './fixtures';
import {
  classifyRefusal,
  classifyStartError,
  daysBetween,
  decimalText,
  formerAuthor,
  historyEntries,
  lastWeek,
  listFilters,
  monthOptions,
  narrativeDiffers,
  narrativeSchema,
  narrativeValues,
  onDemandDefault,
  patchBody,
  REASON_MAX,
  reasonError,
  reasonFieldError,
  SECTION_MAX,
  selfReviewBlocked,
  startBody,
  startErrors,
  startValues,
  statusLabelKey,
  SUMMARY_MAX,
  summaryMissing,
  thisWeek,
  viewMode,
  weekStart,
  type StartValues,
} from './rules';

// #315 H1 (rules): the limits mirror #83 A3; the period rules under a
// frozen `now` in the center zone with a foreign host zone; the body
// mapping; the view mode for every flag combination; the status labels;
// where errors land; self-review; the history.

const CENTER = 'America/Mexico_City';
const HOST_ZONE = 'Asia/Tokyo';
let previousZone: string | undefined;

beforeAll(() => {
  previousZone = process.env.TZ;
  process.env.TZ = HOST_ZONE;
});

afterAll(() => {
  process.env.TZ = previousZone;
});

const STUDENT = '00000000-0000-4000-8000-000000315b01';

function problem(
  code: string,
  status: number,
  extra: {
    errors?: { path: (string | number)[]; code: string }[];
    details?: Record<string, string>;
  } = {},
) {
  return new ApiError({
    code,
    status,
    fieldErrors: (extra.errors ?? []).map((error) => ({
      ...error,
      message: 'x',
    })),
    problem: {
      type: 'about:blank',
      title: 'x',
      status,
      code,
      request_id: 'req',
      ...(extra.details ? { details: extra.details } : {}),
    },
  });
}

describe('limits (#83 A3)', () => {
  it('mirror the API', () => {
    expect(SUMMARY_MAX).toBe(4000);
    expect(SECTION_MAX).toBe(2000);
    expect(REASON_MAX).toBe(1000);
  });

  it('the schema refuses text over the limit, trimmed', () => {
    const ok = {
      summary: 'a'.repeat(4000),
      strengths: ` ${'b'.repeat(2000)} `,
      areas_for_improvement: '',
      next_steps: '',
    };
    expect(narrativeSchema.safeParse(ok).success).toBe(true);
    expect(
      narrativeSchema.safeParse({ ...ok, summary: 'a'.repeat(4001) }).success,
    ).toBe(false);
    expect(
      narrativeSchema.safeParse({ ...ok, next_steps: 'c'.repeat(2001) })
        .success,
    ).toBe(false);
  });
});

describe('the center calendar under a foreign host zone (B3)', () => {
  // Center midnight of Monday 2 Nov 2026 is 06:00Z (UTC−6); Tokyo is
  // already in the afternoon of 2 Nov both before and after it.
  const justAfter = new Date('2026-11-02T06:05:00Z');
  const justBefore = new Date('2026-11-02T05:55:00Z');

  it('today follows the center zone, not the host', () => {
    expect(todayInZone(CENTER, justAfter)).toBe('2026-11-02');
    expect(todayInZone(CENTER, justBefore)).toBe('2026-11-01');
  });

  it('weekly: snaps to the Monday, this and last week', () => {
    expect(weekStart('2026-11-01')).toBe('2026-10-26'); // a Sunday
    expect(weekStart('2026-11-02')).toBe('2026-11-02'); // a Monday
    expect(weekStart('2026-11-04')).toBe('2026-11-02');
    const before = todayInZone(CENTER, justBefore);
    expect(thisWeek(before)).toBe('2026-10-26');
    expect(lastWeek(before)).toBe('2026-10-19');
    const after = todayInZone(CENTER, justAfter);
    expect(thisWeek(after)).toBe('2026-11-02');
    expect(lastWeek(after)).toBe('2026-10-26');
  });

  it('weekly: a Monday after today is refused', () => {
    const before = todayInZone(CENTER, justBefore);
    const values = { ...startValues(before), kind: 'weekly' as const };
    expect(startErrors({ ...values, weekDate: '2026-11-02' }, before)).toEqual({
      week: 'progressReports.start.errors.weekNotStarted',
    });
    expect(startErrors({ ...values, weekDate: '2026-11-01' }, before)).toEqual(
      {},
    );
    const after = todayInZone(CENTER, justAfter);
    expect(startErrors({ ...values, weekDate: '2026-11-04' }, after)).toEqual(
      {},
    );
    expect(startErrors({ ...values, weekDate: '' }, after)).toEqual({
      week: 'progressReports.start.errors.dateRequired',
    });
  });

  it('monthly: the current month and the 12 before it, as the 1st', () => {
    const before = todayInZone(CENTER, justBefore);
    const options = monthOptions(before);
    expect(options).toHaveLength(13);
    expect(options[0]).toBe('2026-11-01');
    expect(options[12]).toBe('2025-11-01');
    expect(monthOptions('2026-10-31')[0]).toBe('2026-10-01');
    expect(monthOptions('2026-01-15')[1]).toBe('2025-12-01');
    const values = { ...startValues(before), kind: 'monthly' as const };
    expect(startErrors({ ...values, month: '2025-10-01' }, before)).toEqual({
      month: 'progressReports.start.errors.monthRequired',
    });
  });

  it('on demand: the last 7 days by default, in the center zone', () => {
    const before = todayInZone(CENTER, justBefore);
    expect(onDemandDefault(before)).toEqual({
      from: '2026-10-26',
      to: '2026-11-01',
    });
    expect(startValues(before)).toMatchObject({
      kind: 'on_demand',
      from: '2026-10-26',
      to: '2026-11-01',
      subjectId: '',
    });
  });

  it('on demand: From after today, To before From, more than 366 days', () => {
    const today = '2026-11-01';
    const base: StartValues = { ...startValues(today) };
    expect(startErrors({ ...base, from: '2026-11-02' }, today).from).toBe(
      'progressReports.start.errors.fromFuture',
    );
    expect(
      startErrors({ ...base, from: '2026-10-10', to: '2026-10-09' }, today).to,
    ).toBe('progressReports.start.errors.toBeforeFrom');
    // 366 days inclusive: accepted; 367: refused.
    expect(daysBetween('2025-01-01', '2026-01-01')).toBe(365);
    expect(
      startErrors({ ...base, from: '2025-01-01', to: '2026-01-01' }, today),
    ).toEqual({});
    expect(
      startErrors({ ...base, from: '2025-01-01', to: '2026-01-02' }, today).to,
    ).toBe('progressReports.start.errors.tooLong');
    // An on-demand period may end after today.
    expect(
      startErrors({ ...base, from: '2026-10-01', to: '2026-11-30' }, today),
    ).toEqual({});
  });
});

describe('the start body (B5)', () => {
  const today = '2026-11-04';
  const base = startValues(today);

  it('weekly: the Monday and no period_end', () => {
    const body = startBody(
      { ...base, kind: 'weekly', weekDate: '2026-11-04' },
      STUDENT,
    );
    expect(body).toEqual({
      student_id: STUDENT,
      kind: 'weekly',
      period_start: '2026-11-02',
    });
    expect('period_end' in body).toBe(false);
    expect(
      startBody({ ...base, kind: 'weekly', weekDate: '2026-11-08' }, STUDENT)
        .period_start,
    ).toBe('2026-11-02');
  });

  it('monthly: the 1st and no period_end', () => {
    expect(
      startBody({ ...base, kind: 'monthly', month: '2026-10-01' }, STUDENT),
    ).toEqual({
      student_id: STUDENT,
      kind: 'monthly',
      period_start: '2026-10-01',
    });
  });

  it('on demand: both dates; a subject only when chosen', () => {
    expect(startBody(base, STUDENT)).toEqual({
      student_id: STUDENT,
      kind: 'on_demand',
      period_start: '2026-10-29',
      period_end: '2026-11-04',
    });
    expect(startBody({ ...base, subjectId: 'sub-1' }, STUDENT)).toMatchObject({
      subject_id: 'sub-1',
    });
    expect('subject_id' in startBody(base, STUDENT)).toBe(false);
  });
});

describe('a refused start (B6)', () => {
  it.each([
    ['weekly', 'period_start', 'week'],
    ['weekly', 'period_end', 'week'],
    ['monthly', 'period_start', 'month'],
    ['on_demand', 'period_start', 'from'],
    ['on_demand', 'period_end', 'to'],
  ] as const)('%s %s → %s', (kind, path, field) => {
    expect(
      classifyStartError(
        problem('validation_error', 422, {
          errors: [{ path: ['body', path], code: 'period_invalid' }],
        }),
        kind,
      ),
    ).toEqual({ kind: 'fields', errors: { [field]: 'errors:period_invalid' } });
  });

  it('period_in_future lands on the start', () => {
    expect(
      classifyStartError(
        problem('validation_error', 422, {
          errors: [
            { path: ['body', 'period_start'], code: 'period_in_future' },
          ],
        }),
        'on_demand',
      ),
    ).toEqual({
      kind: 'fields',
      errors: { from: 'errors:period_in_future' },
    });
  });

  it('the subject codes land on Subject', () => {
    expect(
      classifyStartError(problem('report_subject_not_assigned', 403), 'weekly'),
    ).toEqual({
      kind: 'fields',
      errors: { subject: 'errors:report_subject_not_assigned' },
    });
    expect(
      classifyStartError(
        problem('validation_error', 422, {
          errors: [{ path: ['body', 'subject_id'], code: 'subject_unknown' }],
        }),
        'weekly',
      ),
    ).toEqual({
      kind: 'fields',
      errors: { subject: 'errors:subject_unknown' },
    });
  });

  it('progress_report_exists names the report; 404 and others', () => {
    expect(
      classifyStartError(
        problem('progress_report_exists', 409, {
          details: { report_id: 'r-1' },
        }),
        'weekly',
      ),
    ).toEqual({ kind: 'exists', reportId: 'r-1' });
    expect(classifyStartError(problem('not_found', 404), 'weekly')).toEqual({
      kind: 'missing',
    });
    expect(
      classifyStartError(problem('progress_report_tutor_only', 403), 'weekly'),
    ).toEqual({ kind: 'other' });
    expect(classifyStartError(new Error('x'), 'weekly')).toEqual({
      kind: 'other',
    });
  });
});

describe('the narrative (C2, C3)', () => {
  const baseline = narrativeValues(DRAFT);

  it('reads null as empty', () => {
    expect(baseline).toEqual({
      summary: 'Buen avance en física.',
      strengths: '',
      areas_for_improvement: '',
      next_steps: '',
    });
  });

  it('PATCH sends only the changed keys, trimmed, with the version', () => {
    expect(patchBody(baseline, baseline, 3)).toBeNull();
    expect(
      patchBody(
        { ...baseline, summary: ' Buen avance en física. ' },
        baseline,
        3,
      ),
    ).toBeNull();
    expect(
      patchBody({ ...baseline, strengths: ' Constancia ' }, baseline, 3),
    ).toEqual({ version: 3, strengths: 'Constancia' });
    // A cleared field is sent as "".
    expect(patchBody({ ...baseline, summary: '   ' }, baseline, 4)).toEqual({
      version: 4,
      summary: '',
    });
  });

  it('dirty and blank-summary checks', () => {
    expect(narrativeDiffers(baseline, baseline)).toBe(false);
    expect(narrativeDiffers({ ...baseline, next_steps: 'x' }, baseline)).toBe(
      true,
    );
    expect(summaryMissing({ summary: '  ' })).toBe(true);
    expect(summaryMissing({ summary: 'x' })).toBe(false);
  });
});

describe('view mode (C)', () => {
  const flags = [
    'can_edit',
    'can_approve',
    'can_request_changes',
    'can_retract',
  ] as const;
  const statuses = [
    'draft',
    'submitted',
    'changes_requested',
    'approved_for_family',
    'retracted',
  ] as const;

  it('every flag combination, for a reviewer and not', () => {
    for (let mask = 0; mask < 16; mask += 1) {
      for (const status of statuses) {
        for (const reviewer of [false, true]) {
          const report = {
            status,
            can_edit: Boolean(mask & 1),
            can_approve: Boolean(mask & 2),
            can_request_changes: Boolean(mask & 4),
            can_retract: Boolean(mask & 8),
          };
          const expected = report.can_edit
            ? 'edit'
            : flags.slice(1).some((flag) => report[flag]) ||
                (reviewer && status !== 'draft')
              ? 'review'
              : 'read';
          expect(viewMode(report, { reviewer })).toBe(expected);
        }
      }
    }
  });

  it('the fixtures', () => {
    expect(viewMode(DRAFT, { reviewer: false })).toBe('edit');
    expect(viewMode(CHANGES_REQUESTED, { reviewer: false })).toBe('edit');
    expect(viewMode(SUBMITTED, { reviewer: false })).toBe('read');
    expect(viewMode(REVIEW, { reviewer: true })).toBe('review');
    expect(viewMode(APPROVED_REVIEW, { reviewer: true })).toBe('review');
    expect(viewMode(FORMER_AUTHOR, { reviewer: false })).toBe('read');
    expect(viewMode(OWN_SUBMITTED_REVIEWER, { reviewer: true })).toBe('review');
  });
});

describe('self-review and the former author (E3, C6)', () => {
  it('a reviewer author is blocked, not the Owner', () => {
    expect(
      selfReviewBlocked(OWN_SUBMITTED_REVIEWER, {
        reviewer: true,
        owner: false,
      }),
    ).toBe(true);
    expect(
      selfReviewBlocked(OWN_SUBMITTED_REVIEWER, {
        reviewer: true,
        owner: true,
      }),
    ).toBe(false);
    expect(selfReviewBlocked(REVIEW, { reviewer: true, owner: false })).toBe(
      false,
    );
    expect(
      selfReviewBlocked(
        { ...OWN_SUBMITTED_REVIEWER, status: 'approved_for_family' },
        { reviewer: true, owner: false },
      ),
    ).toBe(false);
    expect(
      selfReviewBlocked(OWN_SUBMITTED_REVIEWER, {
        reviewer: false,
        owner: false,
      }),
    ).toBe(false);
  });

  it('the former author', () => {
    expect(formerAuthor(FORMER_AUTHOR)).toBe(true);
    expect(formerAuthor(DRAFT)).toBe(false);
    expect(formerAuthor(SUBMITTED)).toBe(false);
  });
});

describe('status labels (C1)', () => {
  it.each([
    ['draft', 'progressReports.status.draft'],
    ['submitted', 'progressReports.status.submitted'],
    ['changes_requested', 'progressReports.status.changesRequested'],
    ['approved_for_family', 'progressReports.status.approved'],
    ['retracted', 'progressReports.status.retracted'],
  ] as const)('%s', (status, key) => {
    expect(statusLabelKey(status)).toBe(key);
  });
});

describe('history (C8)', () => {
  it('only the entries R3 has, in time order', () => {
    expect(historyEntries(DRAFT)).toEqual([]);
    expect(historyEntries(RETRACTED).map((entry) => entry.kind)).toEqual([
      'submitted',
      'approved',
      'retracted',
    ]);
    // A resubmit after a request for changes: the last submit comes after.
    const resubmitted = {
      ...SUBMITTED,
      submitted_at: '2026-10-25T18:00:00Z',
      changes_requested: CHANGES_REQUESTED.changes_requested,
    };
    expect(historyEntries(resubmitted).map((entry) => entry.kind)).toEqual([
      'changes',
      'submitted',
    ]);
    expect(historyEntries(APPROVED)[1]).toEqual({
      kind: 'approved',
      at: '2026-10-26T15:00:00Z',
      by: 'Laura Méndez',
    });
  });
});

describe('refusals (C9, E4)', () => {
  it.each([
    ['version_conflict', 409, 'conflict'],
    ['progress_report_under_review', 409, 'state'],
    ['progress_report_closed', 409, 'state'],
    ['progress_report_not_submitted', 409, 'state'],
    ['progress_report_author_only', 403, 'forbidden'],
    ['progress_report_read_only', 403, 'forbidden'],
    ['permission_denied', 403, 'forbidden'],
    ['progress_report_self_review', 403, 'selfReview'],
    ['progress_report_author_not_current', 409, 'authorNotCurrent'],
    ['not_found', 404, 'missing'],
    ['internal_error', 500, 'other'],
  ] as const)('%s → %s', (code, status, kind) => {
    expect(classifyRefusal(problem(code, status)).kind).toBe(kind);
  });

  it('summary_required and field errors land on their fields', () => {
    expect(classifyRefusal(problem('summary_required', 422))).toEqual({
      kind: 'fields',
      errors: { summary: 'progressReports.editor.summaryRequired' },
    });
    expect(
      classifyRefusal(
        problem('validation_error', 422, {
          errors: [
            { path: ['body', 'strengths'], code: 'too_long' },
            { path: ['body', 'next_steps'], code: 'too_long' },
          ],
        }),
      ),
    ).toEqual({
      kind: 'fields',
      errors: { strengths: 'errors:too_long', next_steps: 'errors:too_long' },
    });
  });

  it('the reason: our check and the server codes', () => {
    expect(reasonError('  ')).toBe('errors:reason_required');
    expect(reasonError('a'.repeat(1001))).toBe(
      'progressReports.editor.tooLong',
    );
    expect(reasonError(` ${'a'.repeat(1000)} `)).toBeNull();
    expect(
      reasonFieldError(
        problem('validation_error', 422, {
          errors: [{ path: ['body', 'reason'], code: 'reason_required' }],
        }),
      ),
    ).toBe('errors:reason_required');
    expect(
      reasonFieldError(
        problem('validation_error', 422, {
          errors: [{ path: ['body', 'reason'], code: 'too_long' }],
        }),
      ),
    ).toBe('errors:too_long');
    expect(reasonFieldError(problem('version_conflict', 409))).toBeNull();
  });
});

describe('list filters (E1)', () => {
  const params = (text: string) => new URLSearchParams(text);

  it('a reviewer defaults to the queue', () => {
    expect(listFilters(params(''), { reviewer: true, tutor: false })).toEqual({
      status: 'submitted',
      statusValue: 'submitted',
      kind: null,
      mine: false,
      filtered: false,
    });
    expect(
      listFilters(params('status=all'), { reviewer: true, tutor: false }),
    ).toMatchObject({ status: null, statusValue: 'all', filtered: true });
  });

  it('a tutor defaults to every status, with drafts', () => {
    expect(listFilters(params(''), { reviewer: false, tutor: true })).toEqual({
      status: null,
      statusValue: 'all',
      kind: null,
      mine: false,
      filtered: false,
    });
    expect(
      listFilters(params('status=draft&kind=weekly'), {
        reviewer: false,
        tutor: true,
      }),
    ).toMatchObject({ status: 'draft', kind: 'weekly', filtered: true });
  });

  it('ignores invalid values; mine only for a reviewer tutor', () => {
    expect(
      listFilters(params('status=nope&kind=yearly&mine=true'), {
        reviewer: true,
        tutor: false,
      }),
    ).toMatchObject({ status: 'submitted', kind: null, mine: false });
    expect(
      listFilters(params('status=draft'), { reviewer: true, tutor: false })
        .status,
    ).toBe('submitted');
    expect(
      listFilters(params('mine=true'), { reviewer: true, tutor: true }),
    ).toMatchObject({ mine: true, filtered: true });
  });
});

describe('decimals (D2)', () => {
  it('two places through lib/decimal, never a float', () => {
    expect(decimalText('80.1')).toBe('80.10');
    expect(decimalText('87.50')).toBe('87.50');
    expect(decimalText('100')).toBe('100.00');
    expect(decimalText('n/a')).toBe('n/a');
  });
});
