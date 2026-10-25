import type { FieldError } from '@classora/api-client';
import { ApiError } from '@/app/api';
import { maxScoreError } from '@/lib/decimal';
import { z } from '@/lib/forms';
import {
  EVALUATION_KINDS,
  type EvaluationKind,
  type EvaluationTemplate,
  type EvaluationTemplateCreate,
  type TemplateFilters,
  type TemplateStatus,
} from './api';

// The evaluation template pages' pure rules (#308 B), mirroring the API's
// (#80 C1): the list's filters in the URL, the editor's Zod schema (name
// 1-120, kind, description up to 2000, 1-20 criteria with a title of 1-120
// unique ignoring case, a description up to 500 and a maximum score of 0.5
// to 1000 with at most two places, F1), the request bodies, and the server's
// errors mapped back onto the fields.

export const NAME_MAX = 120;
export const DESCRIPTION_MAX = 2000;
export const CRITERIA_MAX = 20;
export const CRITERION_TITLE_MAX = 120;
export const CRITERION_DESCRIPTION_MAX = 500;

const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;
const STATUSES: readonly TemplateStatus[] = ['active', 'inactive', 'all'];

/** The list's filters from the URL; anything invalid is ignored (B1). */
export function parseTemplateFilters(params: URLSearchParams): TemplateFilters {
  const status = params.get('status');
  const kind = params.get('kind');
  const subject = params.get('subject');
  const q = (params.get('q') ?? '').trim().slice(0, NAME_MAX);
  return {
    status: STATUSES.find((value) => value === status) ?? 'active',
    kind: EVALUATION_KINDS.find((value) => value === kind) ?? null,
    subject:
      subject !== null && UUID.test(subject) ? subject.toLowerCase() : null,
    q,
  };
}

/** The search params of the filters (only what differs from the default). */
export function templateFilterParams(
  filters: TemplateFilters,
): URLSearchParams {
  const params = new URLSearchParams();
  if (filters.status !== 'active') params.set('status', filters.status);
  if (filters.kind) params.set('kind', filters.kind);
  if (filters.subject) params.set('subject', filters.subject);
  if (filters.q) params.set('q', filters.q);
  return params;
}

export function hasTemplateFilters(filters: TemplateFilters): boolean {
  return (
    filters.status !== 'active' ||
    filters.kind !== null ||
    filters.subject !== null ||
    filters.q !== ''
  );
}

// ------------------------------------------------------------------ editor

export interface CriterionValues {
  title: string;
  description: string;
  maxScore: string;
}

export interface TemplateFormValues {
  name: string;
  /** '' until chosen on a create; a template's kind never changes. */
  kind: string;
  /** '' = any subject. */
  subjectId: string;
  description: string;
  criteria: CriterionValues[];
  isActive: boolean;
}

export const EMPTY_CRITERION: CriterionValues = {
  title: '',
  description: '',
  maxScore: '',
};

export const NEW_TEMPLATE: TemplateFormValues = {
  name: '',
  kind: '',
  subjectId: '',
  description: '',
  criteria: [EMPTY_CRITERION],
  isActive: true,
};

/** A stored template as form values. */
export function templateValues(
  template: EvaluationTemplate,
): TemplateFormValues {
  return {
    name: template.name,
    kind: template.kind,
    subjectId: template.subject?.id ?? '',
    description: template.description ?? '',
    criteria: template.criteria.map((criterion) => ({
      title: criterion.title,
      description: criterion.description ?? '',
      maxScore: criterion.max_score,
    })),
    isActive: template.is_active,
  };
}

/**
 * A field message: a `common` key (`academics.templates.validation.*`) or
 * `errors:<code>` for a server code shown with its `errors` message.
 */
export type TemplateMessage = string;

const V = 'academics.templates.validation';

const trimmed = (max: number, required: string | null, tooLong: string) =>
  z.string().superRefine((value, ctx) => {
    const length = value.trim().length;
    if (required && length === 0) {
      ctx.addIssue({ code: 'custom', message: required });
    } else if (length > max) {
      ctx.addIssue({ code: 'custom', message: tooLong });
    }
  });

const MAX_SCORE_MESSAGES = {
  invalid: `${V}.maxScoreInvalid`,
  comma: `${V}.decimalComma`,
  precision: `${V}.maxScorePrecision`,
  range: `${V}.maxScoreRange`,
} as const;

const criterionSchema = z.object({
  title: trimmed(
    CRITERION_TITLE_MAX,
    `${V}.titleRequired`,
    `${V}.titleTooLong`,
  ),
  description: trimmed(
    CRITERION_DESCRIPTION_MAX,
    null,
    `${V}.criterionDescriptionTooLong`,
  ),
  maxScore: z.string().superRefine((value, ctx) => {
    const error = maxScoreError(value);
    if (error) {
      ctx.addIssue({ code: 'custom', message: MAX_SCORE_MESSAGES[error] });
    }
  }),
});

/** The editor's schema (B2). Duplicate titles (ignoring case) are flagged
 * on the later row. */
export const templateFormSchema = z
  .object({
    name: trimmed(NAME_MAX, `${V}.nameRequired`, `${V}.nameTooLong`),
    kind: z.string().superRefine((value, ctx) => {
      if (!EVALUATION_KINDS.some((kind) => kind === value)) {
        ctx.addIssue({ code: 'custom', message: `${V}.kindRequired` });
      }
    }),
    subjectId: z.string(),
    description: trimmed(DESCRIPTION_MAX, null, `${V}.descriptionTooLong`),
    criteria: z
      .array(criterionSchema)
      .min(1, `${V}.criteriaRequired`)
      .max(CRITERIA_MAX, `${V}.criteriaTooMany`),
    isActive: z.boolean(),
  })
  .superRefine((values, ctx) => {
    const seen = new Set<string>();
    values.criteria.forEach((criterion, index) => {
      const key = criterion.title.trim().toLocaleLowerCase();
      if (!key) {
        return;
      }
      if (seen.has(key)) {
        ctx.addIssue({
          code: 'custom',
          path: ['criteria', index, 'title'],
          message: `${V}.duplicateTitle`,
        });
      }
      seen.add(key);
    });
  });

const orNull = (value: string) => {
  const text = value.trim();
  return text === '' ? null : text;
};

/** The create body (POST). The max scores go as the decimal strings typed. */
export function createBody(
  values: TemplateFormValues,
): EvaluationTemplateCreate {
  return {
    name: values.name.trim(),
    kind: values.kind as EvaluationKind,
    subject_id: values.subjectId || null,
    description: orNull(values.description),
    criteria: values.criteria.map((criterion) => ({
      title: criterion.title.trim(),
      description: orNull(criterion.description),
      max_score: criterion.maxScore.trim(),
    })),
  };
}

/** The replace body (PUT), with the version read and is_active. */
export function replaceBody(
  values: TemplateFormValues,
  template: EvaluationTemplate,
) {
  return {
    ...createBody({ ...values, kind: template.kind }),
    version: template.version,
    is_active: values.isActive,
  };
}

/** A field of the form a server error belongs to. */
export type TemplateField =
  | 'name'
  | 'kind'
  | 'subjectId'
  | 'description'
  | 'criteria'
  | `criteria.${number}.title`
  | `criteria.${number}.description`
  | `criteria.${number}.maxScore`;

const CRITERION_FIELDS: Record<string, 'title' | 'description' | 'maxScore'> = {
  title: 'title',
  description: 'description',
  max_score: 'maxScore',
};
const TOP_FIELDS: Record<string, TemplateField> = {
  name: 'name',
  kind: 'kind',
  subject_id: 'subjectId',
  description: 'description',
  criteria: 'criteria',
};

/** The form field of one server field error (by its `path`), if known. */
export function templateFieldOf(error: FieldError): TemplateField | null {
  const [where, top, index, leaf] = error.path;
  if (where !== 'body' || typeof top !== 'string') {
    return null;
  }
  if (top === 'criteria' && typeof index === 'number') {
    const field = typeof leaf === 'string' ? CRITERION_FIELDS[leaf] : undefined;
    return field ? `criteria.${index}.${field}` : 'criteria';
  }
  return TOP_FIELDS[top] ?? null;
}

/** The message of a server field error: ours where we have one. */
export function serverTemplateMessage(code: string): TemplateMessage {
  switch (code) {
    case 'name_required':
      return `${V}.nameRequired`;
    case 'title_required':
      return `${V}.titleRequired`;
    case 'max_score_out_of_range':
      return `${V}.maxScoreRange`;
    case 'score_precision':
      return `${V}.maxScorePrecision`;
    case 'decimal_invalid':
      return `${V}.maxScoreInvalid`;
    case 'duplicate_title':
      return `${V}.duplicateTitle`;
    case 'criteria_required':
      return `${V}.criteriaRequired`;
    case 'criteria_too_many':
      return `${V}.criteriaTooMany`;
    case 'subject_unknown':
      return `${V}.subjectUnknown`;
    case 'kind_immutable':
      return `${V}.kindImmutable`;
    default:
      return `errors:${code}`;
  }
}

/** B2: `409 duplicate_name` lands on Name. */
export function isDuplicateName(error: unknown): boolean {
  return error instanceof ApiError && error.code === 'duplicate_name';
}

/** B2: "This template changed elsewhere." with Reload. */
export function isConflict(error: unknown): boolean {
  return error instanceof ApiError && error.code === 'version_conflict';
}
