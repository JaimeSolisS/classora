import { describe, expect, it } from 'vitest';
import { ApiError } from '@/app/api';
import { maxScoreError } from '@/lib/decimal';
import { ASSIGNMENT, GRADED_ROW, LATE_ROW, PENDING_ROW } from './fixtures';
import {
  assignmentFormSchema,
  classifyFormRefusal,
  classifyRosterRefusal,
  classifySubmitRefusal,
  createBody,
  deadlineParts,
  DUE_MAX_DAYS,
  FEEDBACK_MAX,
  formValuesOf,
  gradeBody,
  gradeErrors,
  INSTRUCTIONS_MAX,
  isSafeHttpUrl,
  latestDueOn,
  lockedRecipients,
  MAX_SCORE_MAX,
  MAX_SCORE_MIN,
  newFormValues,
  parseState,
  patchBody,
  placeServerErrors,
  REASON_MAX,
  RECIPIENTS_MAX,
  TEXT_MAX,
  TITLE_MAX,
  URL_MAX,
  workBody,
  workErrors,
  type AssignmentFormValues,
  type FormContext,
} from './rules';

// #313 G1 (rules): the limits mirror #82 A3; the link rule (C6); the
// deadline parts under a fixed today (E5); the schema (B2-B4); the POST
// and PATCH bodies (explicit student_ids; changed keys only; AM1 null);
// the server's field errors placed by path (B6); the refusals; the grade
// and hand-in checks and bodies.

describe('limits (#82 A3)', () => {
  it('mirror the API', () => {
    expect({
      TITLE_MAX,
      INSTRUCTIONS_MAX,
      TEXT_MAX,
      URL_MAX,
      FEEDBACK_MAX,
      REASON_MAX,
      RECIPIENTS_MAX,
      DUE_MAX_DAYS,
      MAX_SCORE_MIN,
      MAX_SCORE_MAX,
    }).toEqual({
      TITLE_MAX: 200,
      INSTRUCTIONS_MAX: 4000,
      TEXT_MAX: 10000,
      URL_MAX: 2000,
      FEEDBACK_MAX: 4000,
      REASON_MAX: 500,
      RECIPIENTS_MAX: 100,
      DUE_MAX_DAYS: 365,
      MAX_SCORE_MIN: '0.5',
      MAX_SCORE_MAX: '1000',
    });
    // lib/decimal's max-score bounds are the same 0.5 to 1000.
    expect(maxScoreError(MAX_SCORE_MIN)).toBeUndefined();
    expect(maxScoreError(MAX_SCORE_MAX)).toBeUndefined();
    expect(maxScoreError('0.49')).toBe('range');
    expect(maxScoreError('1000.01')).toBe('range');
  });
});

describe('isSafeHttpUrl (C6)', () => {
  it.each([
    ['https://a.b/x', true],
    ['HTTP://A.B', true],
    ['http://example.com:8080/a?b=c#d', true],
    ['https://[::1]/x', true],
    ['javascript:alert(1)', false],
    ['JavaScript:alert(1)', false],
    ['ftp://a.b', false],
    ['/relative', false],
    ['example.com', false],
    ['https://user:pw@a.b', false],
    ['https://user@a.b', false],
    ['http://', false],
    ['https://a.b/x y', false],
    [' https://a.b', false],
    ['https://a.b/\u0007', false],
    ['https://a.b/​', false],
    ['https://a.b\\x', false],
    ['https://a.b:99999/', false],
    ['data:text/html,x', false],
    ['', false],
  ])('%s → %s', (url, safe) => {
    expect(isSafeHttpUrl(url)).toBe(safe);
  });

  it('allows 2000 characters, not 2001', () => {
    const base = 'https://a.b/';
    expect(isSafeHttpUrl(base + 'x'.repeat(2000 - base.length))).toBe(true);
    expect(isSafeHttpUrl(base + 'x'.repeat(2001 - base.length))).toBe(false);
  });
});

describe('deadlineParts (E5)', () => {
  const item = (
    due_on: string,
    state: 'pending' | 'missing' | 'submitted' | 'graded' = 'pending',
    is_late = false,
    due_time: string | null = null,
  ) => ({ due_on, due_time, state, is_late });

  it.each([
    ['today', '2026-11-09', { kind: 'today' }],
    ['tomorrow', '2026-11-10', { kind: 'tomorrow' }],
    ['in 3 days', '2026-11-12', { kind: 'days', days: 3 }],
    ['in 7 days', '2026-11-16', { kind: 'days', days: 7 }],
    ['8 days ahead: none', '2026-11-17', null],
    ['across a month', '2026-12-01', null],
  ] as const)('%s', (_name, dueOn, relative) => {
    expect(deadlineParts(item(dueOn), '2026-11-09').relative).toEqual(relative);
  });

  it('keeps the time as HH:MM and never asks the host zone', () => {
    const parts = deadlineParts(
      item('2026-11-10', 'pending', false, '16:00:00'),
      '2026-11-09',
    );
    expect(parts).toEqual({
      dueOn: '2026-11-10',
      dueTime: '16:00',
      relative: { kind: 'tomorrow' },
      missing: false,
      late: false,
    });
  });

  it('missing and late; no relative part for them', () => {
    expect(
      deadlineParts(item('2026-11-09', 'missing'), '2026-11-09'),
    ).toMatchObject({ missing: true, late: false, relative: null });
    expect(
      deadlineParts(item('2026-11-09', 'submitted', true), '2026-11-09'),
    ).toMatchObject({ missing: false, late: true, relative: null });
    expect(
      deadlineParts(item('2026-11-09', 'graded', true), '2026-11-09'),
    ).toMatchObject({ late: true });
  });
});

describe('the form schema (B2-B4)', () => {
  const context: FormContext = {
    creating: true,
    today: '2026-11-09',
    nowTime: '12:30',
    original: null,
    maxScoreLocked: false,
  };
  const valid: AssignmentFormValues = {
    ...newFormValues('subject-1', ['s-1']),
    title: 'Tarea',
    instructions: 'Haz los ejercicios.',
    dueOn: '2026-11-10',
  };
  const issues = (
    values: Partial<AssignmentFormValues>,
    at: Partial<FormContext> = {},
  ) => {
    const result = assignmentFormSchema({ ...context, ...at }).safeParse({
      ...valid,
      ...values,
    });
    return result.success
      ? {}
      : Object.fromEntries(
          result.error.issues.map((issue) => [
            String(issue.path[0]),
            issue.message,
          ]),
        );
  };
  const V = 'assignments.validation';

  it('accepts a valid create', () => {
    expect(issues({})).toEqual({});
  });

  it.each([
    [{ subjectId: '' }, { subjectId: `${V}.subjectRequired` }],
    [{ title: '   ' }, { title: `${V}.titleRequired` }],
    [{ title: 'x'.repeat(201) }, { title: `${V}.titleTooLong` }],
    [{ instructions: ' ' }, { instructions: `${V}.instructionsRequired` }],
    [
      { instructions: 'x'.repeat(4001) },
      { instructions: `${V}.instructionsTooLong` },
    ],
    [{ dueOn: '' }, { dueOn: `${V}.dueRequired` }],
    [{ dueOn: '2026-11-08' }, { dueOn: `${V}.dueInPast` }],
    [{ dueOn: latestDueOn('2026-11-09') }, {}],
    [{ dueOn: '2027-11-10' }, { dueOn: `${V}.dueTooFar` }],
    [
      { dueOn: '2026-11-09', dueTime: '12:30' },
      { dueTime: `${V}.dueTimePassed` },
    ],
    [{ dueOn: '2026-11-09', dueTime: '12:31' }, {}],
    [{ dueOn: '2026-11-09', dueTime: '' }, {}],
    [{ maxScore: '10.5' }, {}],
    [{ maxScore: '0.4' }, { maxScore: `${V}.maxScoreRange` }],
    [{ maxScore: '7.555' }, { maxScore: `${V}.maxScorePrecision` }],
    [{ maxScore: '7,5' }, { maxScore: `${V}.decimalComma` }],
    [{ maxScore: 'diez' }, { maxScore: `${V}.maxScoreInvalid` }],
    [{ studentIds: [] }, { studentIds: `${V}.recipientsRequired` }],
    [
      { studentIds: Array.from({ length: 101 }, (_, i) => `s-${i}`) },
      { studentIds: `${V}.recipientsLimit` },
    ],
    [{ studentIds: Array.from({ length: 100 }, (_, i) => `s-${i}`) }, {}],
  ] as const)('%j → %j', (values, expected) => {
    expect(issues(values as Partial<AssignmentFormValues>)).toEqual(expected);
  });

  it('an edit checks the deadline only when it changed; a locked max score is not checked', () => {
    const original = { ...valid, dueOn: '2026-10-01', maxScore: '10.00' };
    const edit = { creating: false, original, maxScoreLocked: true };
    expect(issues({ dueOn: '2026-10-01', maxScore: '10.00' }, edit)).toEqual(
      {},
    );
    expect(issues({ dueOn: '2026-10-02', maxScore: '10.00' }, edit)).toEqual({
      dueOn: `${V}.dueInPast`,
    });
    expect(
      issues({ dueOn: '2026-10-01', maxScore: 'x', subjectId: '' }, edit),
    ).toEqual({});
  });
});

describe('bodies (B4, B5)', () => {
  const values: AssignmentFormValues = {
    subjectId: 'subject-1',
    title: '  Tarea  ',
    instructions: ' Haz los ejercicios. ',
    dueOn: '2026-11-10',
    dueTime: '',
    maxScore: '',
    studentIds: ['s-1', 's-2'],
  };

  it('the create sends student_ids always, optional keys only when set', () => {
    expect(createBody(values, null)).toEqual({
      subject_id: 'subject-1',
      title: 'Tarea',
      instructions: 'Haz los ejercicios.',
      due_on: '2026-11-10',
      student_ids: ['s-1', 's-2'],
    });
    expect(
      createBody({ ...values, dueTime: '16:00', maxScore: '10' }, 'session-1'),
    ).toEqual({
      subject_id: 'subject-1',
      title: 'Tarea',
      instructions: 'Haz los ejercicios.',
      due_on: '2026-11-10',
      due_time: '16:00',
      max_score: '10.00',
      class_session_id: 'session-1',
      student_ids: ['s-1', 's-2'],
    });
  });

  it('the edit sends the version and the changed keys only', () => {
    const original = formValuesOf(ASSIGNMENT);
    expect(patchBody(original, original, 3)).toEqual({ version: 3 });
    expect(
      patchBody({ ...original, title: `${original.title} ` }, original, 3),
    ).toEqual({ version: 3 });
    expect(
      patchBody({ ...original, title: 'Nuevo', maxScore: '10' }, original, 3),
    ).toEqual({ version: 3, title: 'Nuevo' });
  });

  it('clearing the time or the max score sends an explicit null (AM1)', () => {
    const original = formValuesOf(ASSIGNMENT);
    expect(original.dueTime).toBe('16:00');
    expect(original.maxScore).toBe('10.00');
    const body = patchBody(
      { ...original, dueTime: '', maxScore: ' ' },
      original,
      3,
    );
    expect(body).toEqual({ version: 3, due_time: null, max_score: null });
    expect(Object.keys(body)).toContain('due_time');
    expect(
      patchBody({ ...original, dueTime: '18:30', maxScore: '20' }, original, 3),
    ).toEqual({ version: 3, due_time: '18:30', max_score: '20.00' });
  });

  it('added and removed students', () => {
    const original = { ...values, studentIds: ['s-1', 's-2'] };
    expect(
      patchBody({ ...original, studentIds: ['s-2', 's-3'] }, original, 1),
    ).toEqual({
      version: 1,
      add_student_ids: ['s-3'],
      remove_student_ids: ['s-1'],
    });
  });

  it('locks the recipients who handed in work', () => {
    expect([...lockedRecipients([PENDING_ROW, LATE_ROW, GRADED_ROW])]).toEqual([
      LATE_ROW.student.id,
      GRADED_ROW.student.id,
    ]);
  });
});

const fieldProblem = (
  errors: { path: (string | number)[]; code: string }[],
  details?: Record<string, string[]>,
) =>
  new ApiError({
    code: 'validation_error',
    status: 422,
    fieldErrors: errors.map((error) => ({ ...error, message: 'x' })),
    problem: {
      type: 'about:blank',
      title: 'x',
      status: 422,
      code: 'validation_error',
      request_id: 'r',
      ...(details ? { details } : {}),
    },
  });

describe('server field errors (B6)', () => {
  it('places each error by its path', () => {
    const placed = placeServerErrors(
      fieldProblem([
        { path: ['body', 'title'], code: 'too_long' },
        { path: ['body', 'instructions'], code: 'instructions_required' },
        { path: ['body', 'max_score'], code: 'score_precision' },
        { path: ['body', 'subject_id'], code: 'subject_unknown' },
        { path: ['body', 'due_on'], code: 'due_too_far' },
        { path: ['body', 'student_ids'], code: 'duplicate_recipient' },
        { path: ['query', 'x'], code: 'ignored' },
      ]),
      { dueOn: '2026-11-10', dueTime: '' },
      '2026-11-09',
    );
    expect(placed.fields).toEqual([
      { field: 'title', message: 'assignments.validation.titleTooLong' },
      {
        field: 'instructions',
        message: 'assignments.validation.instructionsRequired',
      },
      {
        field: 'maxScore',
        message: 'assignments.validation.maxScorePrecision',
      },
      { field: 'subjectId', message: 'assignments.validation.subjectUnknown' },
      { field: 'dueOn', message: 'assignments.validation.dueTooFar' },
      { field: 'studentIds', message: 'errors:duplicate_recipient' },
    ]);
    expect(placed.students.size).toBe(0);
  });

  it('due_in_past for today with a time goes on Due time', () => {
    const error = fieldProblem([
      { path: ['body', 'due_on'], code: 'due_in_past' },
    ]);
    expect(
      placeServerErrors(
        error,
        { dueOn: '2026-11-09', dueTime: '08:00' },
        '2026-11-09',
      ).fields,
    ).toEqual([
      { field: 'dueTime', message: 'assignments.validation.dueTimePassed' },
    ]);
    expect(
      placeServerErrors(
        error,
        { dueOn: '2026-11-08', dueTime: '' },
        '2026-11-09',
      ).fields,
    ).toEqual([
      { field: 'dueOn', message: 'assignments.validation.dueInPast' },
    ]);
  });

  it('recipient_invalid marks every named student', () => {
    const placed = placeServerErrors(
      fieldProblem(
        [{ path: ['body', 'student_ids'], code: 'recipient_invalid' }],
        { student_ids: ['s-2', 's-3'] },
      ),
      { dueOn: '2026-11-10', dueTime: '' },
      '2026-11-09',
    );
    expect(placed.fields).toEqual([
      {
        field: 'studentIds',
        message: 'assignments.validation.recipientInvalidSummary',
      },
    ]);
    expect([...placed.students]).toEqual([
      ['s-2', 'assignments.validation.recipientInvalid'],
      ['s-3', 'assignments.validation.recipientInvalid'],
    ]);
  });
});

describe('refusals', () => {
  const problem = (code: string, status: number) =>
    new ApiError({ code, status });

  it.each([
    [problem('version_conflict', 409), 'conflict'],
    [problem('session_not_assignable', 409), 'session'],
    [
      fieldProblem([
        { path: ['body', 'class_session_id'], code: 'class_session_invalid' },
      ]),
      'session',
    ],
    [
      fieldProblem([
        { path: ['body', 'subject_id'], code: 'subject_mismatch' },
      ]),
      'session',
    ],
    [problem('assignment_has_grades', 409), 'grades'],
    [problem('recipient_has_submission', 409), 'recipientSubmission'],
    [problem('assignment_tutor_only', 403), 'forbidden'],
    [problem('assignment_creator_only', 403), 'forbidden'],
    [problem('assignment_read_only', 403), 'forbidden'],
    [problem('not_found', 404), 'missing'],
    [fieldProblem([{ path: ['body', 'title'], code: 'too_long' }]), 'fields'],
    [problem('internal_error', 500), 'other'],
    [new Error('x'), 'other'],
  ] as const)('the form: %s → %s', (error, refusal) => {
    expect(classifyFormRefusal(error)).toBe(refusal);
  });

  it.each([
    [problem('version_conflict', 409), 'reload'],
    [problem('submission_required', 409), 'reload'],
    [problem('assignment_has_submissions', 409), 'reload'],
    [problem('assignment_read_only', 403), 'forbidden'],
    [problem('not_found', 404), 'missing'],
    [
      fieldProblem([{ path: ['body', 'score'], code: 'score_out_of_range' }]),
      'fields',
    ],
    [problem('internal_error', 500), 'other'],
  ] as const)('the roster: %s → %s', (error, refusal) => {
    expect(classifyRosterRefusal(error)).toBe(refusal);
  });

  it.each([
    [problem('submission_locked', 409), 'locked'],
    [problem('submission_graded', 409), 'graded'],
    [problem('version_conflict', 409), 'conflict'],
    [
      fieldProblem([{ path: ['body', 'version'], code: 'version_required' }]),
      'conflict',
    ],
    [problem('student_consent_required', 403), 'consent'],
    [problem('not_found', 404), 'missing'],
    [fieldProblem([{ path: ['body', 'url'], code: 'url_invalid' }]), 'fields'],
    [problem('submission_student_only', 403), 'other'],
  ] as const)('a submission: %s → %s', (error, refusal) => {
    expect(classifySubmitRefusal(error)).toBe(refusal);
  });
});

describe('the grade (C5)', () => {
  const V = 'assignments.validation';
  it('scored: a score 0..max with two decimals, never parseFloat', () => {
    const errors = (score: string) =>
      gradeErrors(
        { score, feedback: '', reason: '' },
        { maxScore: '10.00', regrade: false },
      );
    expect(errors('')).toEqual({ score: `${V}.scoreRequired` });
    expect(errors('10')).toEqual({});
    expect(errors('0')).toEqual({});
    expect(errors('10.01')).toEqual({ score: `${V}.scoreRange` });
    expect(errors('9.999')).toEqual({ score: `${V}.scorePrecision` });
    expect(errors('9,5')).toEqual({ score: `${V}.decimalComma` });
    expect(errors('-1')).toEqual({ score: `${V}.scoreInvalid` });
  });

  it('feedback only needs feedback; a re-grade needs a reason', () => {
    expect(
      gradeErrors(
        { score: '', feedback: ' ', reason: '' },
        { maxScore: null, regrade: false },
      ),
    ).toEqual({ feedback: `${V}.feedbackRequired` });
    expect(
      gradeErrors(
        { score: '8', feedback: 'x'.repeat(4001), reason: ' ' },
        { maxScore: '10.00', regrade: true },
      ),
    ).toEqual({
      feedback: `${V}.feedbackTooLong`,
      reason: `${V}.reasonRequired`,
    });
    expect(
      gradeErrors(
        { score: '8', feedback: '', reason: 'x'.repeat(501) },
        { maxScore: '10.00', regrade: true },
      ),
    ).toEqual({ reason: `${V}.reasonTooLong` });
  });

  it('change_reason only on a re-grade; the score as a decimal string', () => {
    expect(
      gradeBody(
        { score: '8', feedback: ' Bien ', reason: 'ignored' },
        { version: 2, maxScore: '10.00', regrade: false },
      ),
    ).toEqual({ version: 2, score: '8.00', feedback: 'Bien' });
    expect(
      gradeBody(
        { score: '8.5', feedback: '', reason: ' Revisé ' },
        { version: 4, maxScore: '10.00', regrade: true },
      ),
    ).toEqual({
      version: 4,
      score: '8.50',
      feedback: null,
      change_reason: 'Revisé',
    });
    expect(
      gradeBody(
        { score: '', feedback: 'Bien', reason: '' },
        { version: 1, maxScore: null, regrade: false },
      ),
    ).toEqual({ version: 1, feedback: 'Bien' });
  });
});

describe('hand-in (E6, C4)', () => {
  const V = 'assignments.validation';
  it('one of text or link; the link rule; trimmed empty is empty', () => {
    expect(workErrors({ text: ' ', url: ' ' }, { requireOne: true })).toEqual({
      root: `${V}.workRequired`,
    });
    expect(workErrors({ text: ' ', url: '' }, { requireOne: false })).toEqual(
      {},
    );
    expect(
      workErrors({ text: '', url: 'javascript:x' }, { requireOne: true }),
    ).toEqual({ url: `${V}.urlInvalid` });
    expect(
      workErrors({ text: 'x'.repeat(10001), url: '' }, { requireOne: true }),
    ).toEqual({ text: `${V}.textTooLong` });
  });

  it('the version only for a replacement', () => {
    expect(workBody({ text: ' Hola ', url: '' }, null)).toEqual({
      text: 'Hola',
      url: null,
    });
    expect(workBody({ text: '', url: 'https://a.b' }, 3)).toEqual({
      version: 3,
      text: null,
      url: 'https://a.b',
    });
  });
});

describe('parseState', () => {
  it('reads a state or null', () => {
    expect(parseState('missing')).toBe('missing');
    expect(parseState('nope')).toBeNull();
    expect(parseState(null)).toBeNull();
  });
});
