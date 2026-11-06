import openapi from '@classora/api-client/openapi.json';
import { describe, expect, it } from 'vitest';
import { ENUM_LABELS, UNLABELLED_ENUMS } from './enumLabels';
import { i18n } from '.';
import { LANGUAGES } from './language';

// #130 C2 (E3): every enum of the API contract is classified once, and every
// value of a labelled enum has a label in es and en. Query-parameter enums
// are out of scope (filter values, covered by their pages' tests).

interface Schema {
  enum?: (string | number)[];
  type?: string;
  items?: Schema;
  anyOf?: Schema[];
  oneOf?: Schema[];
  properties?: Record<string, Schema>;
}

const schemas = (
  openapi as unknown as { components: { schemas: Record<string, Schema> } }
).components.schemas;

/** The values of an inline enum: direct, through `anyOf`/`oneOf` (an
 * optional field) or of an array's items. */
function inlineValues(schema: Schema): (string | number)[] | undefined {
  if (schema.enum) {
    return schema.enum;
  }
  if (schema.type === 'array' && schema.items?.enum) {
    return schema.items.enum;
  }
  for (const option of [...(schema.anyOf ?? []), ...(schema.oneOf ?? [])]) {
    const values = inlineValues(option);
    if (values) {
      return values;
    }
  }
  return undefined;
}

/** Every enum id of the contract with its values (as strings). */
function contractEnums(): Map<string, string[]> {
  const enums = new Map<string, string[]>();
  for (const [name, schema] of Object.entries(schemas)) {
    if (schema.enum) {
      enums.set(name, schema.enum.map(String));
    }
    for (const [property, field] of Object.entries(schema.properties ?? {})) {
      const values = inlineValues(field);
      if (values) {
        enums.set(`${name}.${property}`, values.map(String));
      }
    }
  }
  return enums;
}

function exists(key: string, language: string): boolean {
  const [ns, name]: ['common' | 'errors', string] = key.startsWith('errors:')
    ? ['errors', key.slice('errors:'.length)]
    : ['common', key];
  if (i18n.exists(name, { lng: language, ns })) {
    return true;
  }
  // A plural-only message (`_one`, `_other`; es also `_many`).
  const forms = new Intl.PluralRules(language).resolvedOptions()
    .pluralCategories;
  return forms.every((form) =>
    i18n.exists(`${name}_${form}`, { lng: language, ns }),
  );
}

const enums = contractEnums();

describe('enum classification (#130 C2)', () => {
  it('finds the enums of the contract', () => {
    expect(enums.size).toBeGreaterThan(150);
    expect(enums.get('SessionRequestDuration')).toEqual([
      '30',
      '45',
      '60',
      '90',
      '120',
    ]);
  });

  it('classifies every enum in exactly one map', () => {
    const problems = [...enums.keys()].flatMap((id) => {
      const labelled = id in ENUM_LABELS;
      const unlabelled = id in UNLABELLED_ENUMS;
      if (labelled && unlabelled) {
        return [`${id}: in both ENUM_LABELS and UNLABELLED_ENUMS`];
      }
      if (!labelled && !unlabelled) {
        return [
          `${id}: not classified (add it to ENUM_LABELS or UNLABELLED_ENUMS)`,
        ];
      }
      return [];
    });
    expect(problems, `\n${problems.join('\n')}\n`).toEqual([]);
  });

  it('names no enum the contract no longer has', () => {
    const stale = [
      ...Object.keys(ENUM_LABELS),
      ...Object.keys(UNLABELLED_ENUMS),
    ].filter((id) => !enums.has(id));
    expect(stale, `\n${stale.join('\n')}\n`).toEqual([]);
  });

  it('gives each unlabelled enum a reason', () => {
    for (const [id, reason] of Object.entries(UNLABELLED_ENUMS)) {
      expect(reason.trim(), id).not.toBe('');
    }
  });

  it('labels every value of every labelled enum in es and en', () => {
    const missing = [...enums].flatMap(([id, values]) => {
      const key = ENUM_LABELS[id];
      if (!key) {
        return [];
      }
      return values.flatMap((value) =>
        LANGUAGES.filter((language) => !exists(key(value), language)).map(
          (language) => `${id} ${language}: ${value} (${key(value)})`,
        ),
      );
    });
    expect(missing, `\n${missing.join('\n')}\n`).toEqual([]);
  });
});
