import { describe, expect, it } from 'vitest';
import { ApiError } from '@/app/api';
import {
  classifyScheduleError,
  currentPeriodStart,
  firstPeriodOptions,
  scheduleBody,
  scheduleFormSchema,
} from './scheduleRules';

// #316 E3, E5: the first period choices (the center's calendar, from an
// injected today) and where a refused add or change lands.

describe('first periods (E3)', () => {
  it.each([
    ['2026-10-19', '2026-10-19'],
    ['2026-10-21', '2026-10-19'],
    ['2026-10-25', '2026-10-19'],
    ['2026-01-01', '2025-12-29'],
  ])('weekly on %s starts on %s', (today, monday) => {
    expect(currentPeriodStart('weekly', today)).toBe(monday);
    const options = firstPeriodOptions('weekly', today);
    expect(options).toHaveLength(12);
    expect(options[0]).toBe(monday);
    expect(options[1]).toBe(
      new Date(Date.parse(`${monday}T00:00:00Z`) + 7 * 86_400_000)
        .toISOString()
        .slice(0, 10),
    );
  });

  it('monthly: this month and the next 11, across the year', () => {
    expect(currentPeriodStart('monthly', '2026-10-31')).toBe('2026-10-01');
    expect(firstPeriodOptions('monthly', '2026-10-31')).toEqual([
      '2026-10-01',
      '2026-11-01',
      '2026-12-01',
      '2027-01-01',
      '2027-02-01',
      '2027-03-01',
      '2027-04-01',
      '2027-05-01',
      '2027-06-01',
      '2027-07-01',
      '2027-08-01',
      '2027-09-01',
    ]);
  });
});

describe('the form (E3)', () => {
  it('requires a tutor and a first period', () => {
    const result = scheduleFormSchema.safeParse({
      author: '',
      cadence: 'weekly',
      subject: '',
      firstPeriod: '',
    });
    expect(result.success).toBe(false);
    expect(result.error!.issues.map((issue) => issue.message).sort()).toEqual([
      'progressReports.schedule.errors.firstPeriodRequired',
      'progressReports.schedule.errors.tutorRequired',
    ]);
  });

  it('the body: no subject_id for all subjects', () => {
    expect(
      scheduleBody({
        author: 'u-1',
        cadence: 'monthly',
        subject: '',
        firstPeriod: '2026-11-01',
      }),
    ).toEqual({
      author_user_id: 'u-1',
      cadence: 'monthly',
      starts_on: '2026-11-01',
    });
    expect(
      scheduleBody({
        author: 'u-1',
        cadence: 'weekly',
        subject: 's-1',
        firstPeriod: '2026-10-19',
      }).subject_id,
    ).toBe('s-1');
  });
});

describe('refusals (E5)', () => {
  const problem = (code: string, fieldErrors = [] as ApiError['fieldErrors']) =>
    new ApiError({ code, status: 422, fieldErrors });

  it('field errors by body path, the first per field', () => {
    expect(
      classifyScheduleError(
        problem('validation_error', [
          {
            path: ['body', 'author_user_id'],
            code: 'schedule_author_invalid',
            message: '',
          },
          {
            path: ['body', 'subject_id'],
            code: 'subject_unknown',
            message: '',
          },
          {
            path: ['body', 'subject_id'],
            code: 'schedule_subject_not_assigned',
            message: '',
          },
          {
            path: ['body', 'starts_on'],
            code: 'schedule_starts_on_invalid',
            message: '',
          },
        ]),
      ),
    ).toEqual({
      kind: 'fields',
      errors: {
        author_user_id: 'errors:schedule_author_invalid',
        subject_id: 'errors:subject_unknown',
        starts_on: 'errors:schedule_starts_on_invalid',
      },
    });
  });

  it('exists, conflict and anything else', () => {
    expect(
      classifyScheduleError(problem('progress_report_schedule_exists')),
    ).toEqual({
      kind: 'exists',
    });
    expect(classifyScheduleError(problem('version_conflict'))).toEqual({
      kind: 'conflict',
    });
    expect(classifyScheduleError(problem('student_inactive'))).toEqual({
      kind: 'other',
    });
    expect(
      classifyScheduleError(
        problem('validation_error', [
          { path: ['body', 'cadence'], code: 'literal_error', message: '' },
        ]),
      ),
    ).toEqual({ kind: 'other' });
    expect(classifyScheduleError(new Error('x'))).toEqual({ kind: 'other' });
  });
});
