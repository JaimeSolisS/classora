import inventory from '@classora/api-client/i18n-inventory.json';
import { describe, expect, it } from 'vitest';
import {
  codeMessageKey,
  GENERIC_FIELD_CODES,
  LOCAL_ONLY_CODES,
  MAPPED_ERROR_CODES,
} from '@/app/errors';
import { rowCodeLabelKey } from '@/features/imports/codes';
import { isLabelledTask, taskLabelKey } from '@/features/jobs/api';
import { notificationMessageKey } from '@/features/notifications/messages';
import { preferenceLabelKey } from '@/features/notifications/preferenceTypes';
import { i18n } from '.';
import { LANGUAGES } from './language';

// #130 C1: every code the API can send (the generated
// packages/api-client/i18n-inventory.json, `make api-client`) has a label in
// es and en, through the same key builder the UI uses. Each failure lists
// every gap as "<area> <language>: <code>".

type Inventory = Record<
  | 'field_codes'
  | 'import_row_codes'
  | 'job_tasks'
  | 'notification_types'
  | 'problem_codes',
  string[]
>;
const codes = inventory as Inventory;

function exists(key: string, language: string, ns = 'common'): boolean {
  return i18n.exists(key, { lng: language, ns });
}

/** A key or a complete plural family (`_one`, `_other`; es also `_many`). */
function existsOrPlural(key: string, language: string): boolean {
  if (exists(key, language)) {
    return true;
  }
  const forms = new Intl.PluralRules(language).resolvedOptions()
    .pluralCategories;
  return forms.every((form) => exists(`${key}_${form}`, language));
}

/** The gaps of one area, as "<area> <language>: <code>". */
function gaps(
  area: string,
  values: readonly string[],
  check: (code: string, language: string) => boolean,
): string[] {
  return values.flatMap((code) =>
    LANGUAGES.filter((language) => !check(code, language)).map(
      (language) => `${area} ${language}: ${code}`,
    ),
  );
}

function expectNoGaps(found: string[]): void {
  expect(found, `\n${found.join('\n')}\n`).toEqual([]);
}

describe('the i18n inventory (#130 C1)', () => {
  it('has every area, each sorted and non-empty', () => {
    expect(Object.keys(codes).sort()).toEqual([
      'field_codes',
      'import_row_codes',
      'job_tasks',
      'notification_types',
      'problem_codes',
    ]);
    for (const values of Object.values(codes)) {
      expect(values.length).toBeGreaterThan(0);
      expect(values).toEqual([...new Set(values)].sort());
    }
  });

  it('maps every problem code centrally, in es and en', () => {
    const mapped = new Set(MAPPED_ERROR_CODES);
    const found = gaps(
      'problem_codes',
      codes.problem_codes,
      (code, language) => {
        if (LOCAL_ONLY_CODES.includes(code)) {
          return true;
        }
        const key = codeMessageKey(code);
        return (
          mapped.has(code) &&
          key !== undefined &&
          exists(key, language, 'errors')
        );
      },
    );
    expectNoGaps(found);
  });

  it('lists only local-only codes the API has', () => {
    for (const code of LOCAL_ONLY_CODES) {
      expect(codes.problem_codes).toContain(code);
    }
  });

  it('gives every field code a message, unless it is a generic Pydantic code', () => {
    const found = gaps('field_codes', codes.field_codes, (code, language) => {
      if (GENERIC_FIELD_CODES.includes(code)) {
        return true;
      }
      const key = codeMessageKey(code);
      return key !== undefined && exists(key, language, 'errors');
    });
    expectNoGaps(found);
  });

  it('gives every notification type a message and a preference label', () => {
    expectNoGaps([
      ...gaps(
        'notification message',
        codes.notification_types,
        (type, language) =>
          existsOrPlural(notificationMessageKey(type), language),
      ),
      ...gaps(
        'notification preference',
        codes.notification_types,
        (type, language) => exists(preferenceLabelKey(type), language),
      ),
    ]);
  });

  it('labels every job task', () => {
    const found = gaps(
      'job_tasks',
      codes.job_tasks,
      (task, language) =>
        isLabelledTask(task) && exists(taskLabelKey(task), language),
    );
    expectNoGaps(found);
  });

  it('labels every import row code', () => {
    const found = gaps(
      'import_row_codes',
      codes.import_row_codes,
      (code, language) => {
        const key = rowCodeLabelKey(code);
        return key !== undefined && exists(key, language);
      },
    );
    expectNoGaps(found);
  });
});
