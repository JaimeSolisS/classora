// Missing-key check for the translation catalogs. Pure: it gets the parsed
// catalogs and returns every problem, so the same function serves the real
// catalogs (check.test.ts, run by `make test-web` and CI) and the fixtures.

/** Parsed catalogs: language → namespace → JSON content. */
export type Catalogs = Record<string, Record<string, unknown>>;

export type ProblemKind =
  | 'missing-namespace'
  | 'missing-key'
  | 'invalid-value'
  | 'placeholder-mismatch'
  | 'missing-plural-form';

export interface CatalogProblem {
  kind: ProblemKind;
  language: string;
  namespace: string;
  /** Dotted key path (empty for a missing namespace). */
  key: string;
  message: string;
}

// i18next plural suffixes (v21+ JSON format): key_one, key_many, key_other…
const PLURAL_SUFFIX = /^(.+)_(zero|one|two|few|many|other)$/;
const PLACEHOLDER = /\{\{\s*-?\s*([^,}\s]+)[^}]*\}\}/g;

type Leaves = Map<string, unknown>;

/** Flattens nested objects to dotted key paths; any non-object is a leaf. */
function flatten(value: unknown, prefix = '', out: Leaves = new Map()) {
  if (typeof value === 'object' && value !== null && !Array.isArray(value)) {
    for (const [key, child] of Object.entries(value)) {
      flatten(child, prefix ? `${prefix}.${key}` : key, out);
    }
  } else {
    out.set(prefix, value);
  }
  return out;
}

function placeholders(value: unknown): string[] {
  if (typeof value !== 'string') {
    return [];
  }
  return [...new Set([...value.matchAll(PLACEHOLDER)].map((m) => m[1]!))]
    .filter(Boolean)
    .sort();
}

interface Entry {
  /** Leaf keys that make up the entry (one, or a plural family). */
  forms: Map<string, unknown>;
  plural: boolean;
}

/** Groups leaves into plain keys and plural families (by base key). */
function entries(leaves: Leaves, categories: readonly string[]) {
  const result = new Map<string, Entry>();
  for (const [key, value] of leaves) {
    const match = PLURAL_SUFFIX.exec(key);
    const isPlural = match !== null && categories.includes(match[2]!);
    const base = isPlural ? match[1]! : key;
    const entry = result.get(base) ?? { forms: new Map(), plural: isPlural };
    entry.forms.set(key, value);
    entry.plural ||= isPlural;
    result.set(base, entry);
  }
  return result;
}

const ALL_CATEGORIES = ['zero', 'one', 'two', 'few', 'many', 'other'];

function pluralCategories(language: string): readonly string[] {
  return new Intl.PluralRules(language).resolvedOptions().pluralCategories;
}

function joinedPlaceholders(entry: Entry): string {
  const all = new Set<string>();
  for (const value of entry.forms.values()) {
    placeholders(value).forEach((name) => all.add(name));
  }
  return [...all].sort().join(', ') || 'none';
}

/** Compares every language's catalogs with each other. */
export function checkCatalogs(catalogs: Catalogs): CatalogProblem[] {
  const problems: CatalogProblem[] = [];
  const languages = Object.keys(catalogs).sort();
  const namespaces = [
    ...new Set(languages.flatMap((lang) => Object.keys(catalogs[lang]!))),
  ].sort();

  for (const namespace of namespaces) {
    const present = languages.filter((lang) => namespace in catalogs[lang]!);
    for (const language of languages) {
      if (!present.includes(language)) {
        problems.push({
          kind: 'missing-namespace',
          language,
          namespace,
          key: '',
          message: `namespace file "${namespace}" is missing`,
        });
      }
    }

    const byLanguage = new Map(
      present.map((language) => [
        language,
        entries(flatten(catalogs[language]![namespace]), ALL_CATEGORIES),
      ]),
    );

    // Values: non-empty strings only.
    for (const [language, langEntries] of byLanguage) {
      for (const entry of langEntries.values()) {
        for (const [key, value] of entry.forms) {
          if (typeof value !== 'string' || value.trim() === '') {
            problems.push({
              kind: 'invalid-value',
              language,
              namespace,
              key,
              message:
                typeof value === 'string'
                  ? 'value is empty'
                  : `value is ${value === null ? 'null' : Array.isArray(value) ? 'an array' : typeof value}, not a string`,
            });
          }
        }
      }
    }

    const keys = [
      ...new Set([...byLanguage.values()].flatMap((e) => [...e.keys()])),
    ].sort();

    for (const key of keys) {
      const owners = present.filter((lang) => byLanguage.get(lang)!.has(key));
      for (const language of present) {
        if (!owners.includes(language)) {
          problems.push({
            kind: 'missing-key',
            language,
            namespace,
            key,
            message: `key is missing (present in ${owners.join(', ')})`,
          });
        }
      }

      // Plural families: each language needs every category it uses.
      for (const language of owners) {
        const entry = byLanguage.get(language)!.get(key)!;
        const isPlural = present.some(
          (lang) => byLanguage.get(lang)!.get(key)?.plural,
        );
        if (!isPlural) {
          continue;
        }
        for (const category of pluralCategories(language)) {
          const form = `${key}_${category}`;
          if (!entry.forms.has(form)) {
            problems.push({
              kind: 'missing-plural-form',
              language,
              namespace,
              key: form,
              message: `plural form "${category}" is missing`,
            });
          }
        }
      }

      // Placeholders must match across languages.
      if (owners.length > 1) {
        const reference = owners[0]!;
        const expected = joinedPlaceholders(
          byLanguage.get(reference)!.get(key)!,
        );
        for (const language of owners.slice(1)) {
          const actual = joinedPlaceholders(
            byLanguage.get(language)!.get(key)!,
          );
          if (actual !== expected) {
            problems.push({
              kind: 'placeholder-mismatch',
              language,
              namespace,
              key,
              message: `placeholders {${actual}} differ from ${reference} {${expected}}`,
            });
          }
        }
      }
    }
  }
  return problems;
}

/** One readable line per problem, for test failure output. */
export function formatProblems(problems: readonly CatalogProblem[]): string {
  return problems
    .map(
      (p) =>
        `${p.language}/${p.namespace}${p.key ? `: ${p.key}` : ''} — ${p.message}`,
    )
    .join('\n');
}
