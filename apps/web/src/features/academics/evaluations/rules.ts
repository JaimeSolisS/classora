import { ApiError } from '@/app/api';
import { formatHundredths, parseScore, scoreError } from '@/lib/decimal';
import type { LocalDate } from '@/lib/time';
import type { Evaluation, EvaluationCreate, EvaluationPatch } from './api';

// The evaluation form's pure rules (#308 C), mirroring the API's (#80 D):
// the values a tutor types, their checks (a score from 0 to the criterion's
// maximum with at most two places, F1, empty only in a draft; comments up
// to 1000; feedback up to 4000; the change reason 1-500; the date never
// after today), the request bodies (decimal strings, never a float), which
// criteria are still unscored, the status text, and the server's errors
// sorted into what the page shows (C8). The client never computes totals,
// percentages or averages.

export const COMMENT_MAX = 1000;
export const FEEDBACK_MAX = 4000;
export const REASON_MAX = 500;

/** A criterion as the form shows it: from a template or an evaluation. */
export interface FormCriterion {
  position: number;
  title: string;
  description: string | null;
  /** The API's decimal string ("10.00"). */
  max_score: string;
}

export interface ScoreValues {
  position: number;
  score: string;
  comment: string;
}

export interface EvaluationValues {
  evaluatedOn: string;
  scores: ScoreValues[];
  feedback: string;
  changeReason: string;
}

/** A field message key under `academics.evaluations.validation`, or
 * `errors:<code>` for a server code with its `errors` message. */
export type EvaluationMessage = string;

export interface EvaluationErrors {
  template?: EvaluationMessage;
  subject?: EvaluationMessage;
  evaluatedOn?: EvaluationMessage;
  feedback?: EvaluationMessage;
  changeReason?: EvaluationMessage;
  /** By criterion position. */
  scores: Record<
    number,
    { score?: EvaluationMessage; comment?: EvaluationMessage }
  >;
}

export const NO_ERRORS: EvaluationErrors = { scores: {} };

const V = 'academics.evaluations.validation';

export function emptyScores(criteria: readonly FormCriterion[]): ScoreValues[] {
  return [...criteria]
    .sort((a, b) => a.position - b.position)
    .map((criterion) => ({
      position: criterion.position,
      score: '',
      comment: '',
    }));
}

/** An evaluation's copied criteria, as the form shows them (C4). */
export function criteriaOf(evaluation: Evaluation): FormCriterion[] {
  return evaluation.scores.map((score) => ({
    position: score.position,
    title: score.title,
    description: score.description,
    max_score: score.max_score,
  }));
}

/** An evaluation's stored values, as the form edits them. */
export function evaluationValues(evaluation: Evaluation): EvaluationValues {
  return {
    evaluatedOn: evaluation.evaluated_on,
    scores: [...evaluation.scores]
      .sort((a, b) => a.position - b.position)
      .map((score) => ({
        position: score.position,
        score: score.score ?? '',
        comment: score.comment ?? '',
      })),
    feedback: evaluation.feedback ?? '',
    changeReason: '',
  };
}

export function hasErrors(errors: EvaluationErrors): boolean {
  return (
    Boolean(
      errors.template ||
      errors.subject ||
      errors.evaluatedOn ||
      errors.feedback ||
      errors.changeReason,
    ) ||
    Object.values(errors.scores).some((entry) =>
      Boolean(entry.score || entry.comment),
    )
  );
}

const SCORE_MESSAGES = {
  invalid: `${V}.scoreInvalid`,
  comma: `${V}.decimalComma`,
  precision: `${V}.scorePrecision`,
  range: `${V}.scoreRange`,
} as const;

export interface ValidateOptions {
  criteria: readonly FormCriterion[];
  /** `amend`: every criterion keeps a score and the reason is required. */
  mode: 'draft' | 'amend';
  today: LocalDate;
}

/** The form's checks (C2, C5, C1 "Evaluated on"). */
export function validateEvaluation(
  values: EvaluationValues,
  { criteria, mode, today }: ValidateOptions,
): EvaluationErrors {
  const errors: EvaluationErrors = { scores: {} };
  if (!/^\d{4}-\d{2}-\d{2}$/.test(values.evaluatedOn)) {
    errors.evaluatedOn = `${V}.dateRequired`;
  } else if (values.evaluatedOn > today) {
    errors.evaluatedOn = `${V}.dateInFuture`;
  }
  for (const criterion of criteria) {
    const entry = values.scores.find((s) => s.position === criterion.position);
    const score = entry?.score.trim() ?? '';
    const found: { score?: string; comment?: string } = {};
    if (score === '') {
      if (mode === 'amend') {
        found.score = `${V}.scoreRequired`;
      }
    } else {
      const error = scoreError(score, criterion.max_score);
      if (error) {
        found.score = SCORE_MESSAGES[error];
      }
    }
    if ((entry?.comment.trim().length ?? 0) > COMMENT_MAX) {
      found.comment = `${V}.commentTooLong`;
    }
    if (found.score || found.comment) {
      errors.scores[criterion.position] = found;
    }
  }
  if (values.feedback.trim().length > FEEDBACK_MAX) {
    errors.feedback = `${V}.feedbackTooLong`;
  }
  if (mode === 'amend') {
    const length = values.changeReason.trim().length;
    if (length === 0) {
      errors.changeReason = `${V}.reasonRequired`;
    } else if (length > REASON_MAX) {
      errors.changeReason = `${V}.reasonTooLong`;
    }
  }
  return errors;
}

/** The criteria without a score, in order (C5: Finalize is blocked). */
export function missingCriteria(
  values: EvaluationValues,
  criteria: readonly FormCriterion[],
): FormCriterion[] {
  return [...criteria]
    .sort((a, b) => a.position - b.position)
    .filter(
      (criterion) =>
        (values.scores
          .find((s) => s.position === criterion.position)
          ?.score.trim() ?? '') === '',
    );
}

const orNull = (value: string) => {
  const text = value.trim();
  return text === '' ? null : text;
};

/** The typed score as the API's decimal string ("8" → "8.00"), or null. */
export function scoreText(value: string): string | null {
  const parsed = parseScore(value);
  return value.trim() === '' || !parsed.ok
    ? null
    : formatHundredths(parsed.hundredths);
}

/** Every criterion's score and comment, in position order. */
export function scoreInputs(
  values: EvaluationValues,
): NonNullable<EvaluationCreate['scores']> {
  return [...values.scores]
    .sort((a, b) => a.position - b.position)
    .map((entry) => ({
      position: entry.position,
      score: scoreText(entry.score),
      comment: orNull(entry.comment),
    }));
}

export interface CreateLinks {
  studentId: string;
  templateId: string;
  /** Sent when the template has no subject of its own. */
  subjectId: string | null;
  classSessionId: string | null;
  courseAssessmentId: string | null;
}

/** The create body (C3). */
export function createBody(
  values: EvaluationValues,
  links: CreateLinks,
): EvaluationCreate {
  return {
    student_id: links.studentId,
    template_id: links.templateId,
    ...(links.subjectId ? { subject_id: links.subjectId } : {}),
    evaluated_on: values.evaluatedOn,
    ...(links.classSessionId ? { class_session_id: links.classSessionId } : {}),
    ...(links.courseAssessmentId
      ? { course_assessment_id: links.courseAssessmentId }
      : {}),
    scores: scoreInputs(values),
    feedback: orNull(values.feedback),
  };
}

/** The PATCH body of a draft save (C3) or an amendment (C5). */
export function patchBody(
  values: EvaluationValues,
  version: number,
  mode: 'draft' | 'amend',
): EvaluationPatch {
  return {
    version,
    evaluated_on: values.evaluatedOn,
    scores: scoreInputs(values),
    feedback: orNull(values.feedback),
    ...(mode === 'amend' ? { change_reason: values.changeReason.trim() } : {}),
  };
}

/** Whether two value sets differ (the leave check, "save first"). */
export function valuesDiffer(
  a: EvaluationValues,
  b: EvaluationValues,
): boolean {
  return JSON.stringify(a) !== JSON.stringify(b);
}

export type StatusKey = 'draft' | 'final' | 'finalUpdated' | 'voided';

/** C4: "Draft", "Final", "Final · Updated" or "Voided". */
export function statusKey(
  evaluation: Pick<Evaluation, 'status' | 'amended_at' | 'voided'>,
): StatusKey {
  if (evaluation.voided) {
    return 'voided';
  }
  if (evaluation.status === 'draft') {
    return 'draft';
  }
  return evaluation.amended_at ? 'finalUpdated' : 'final';
}

// ------------------------------------------------------------ server errors

const FIELD_CODES = new Set([
  'score_out_of_range',
  'score_precision',
  'decimal_invalid',
  'too_long',
  'change_reason_required',
  'score_required',
  'reason_required',
  'evaluated_on_in_future',
  'evaluated_on_invalid',
  'subject_required',
  'subject_unknown',
  'subject_mismatch',
]);

const STALE_CODES = new Set([
  'course_assessment_unknown',
  'course_assessment_requires_course_session',
  'criterion_unknown',
  'duplicate_criterion',
]);

const STATE_CODES = new Set([
  'evaluation_final',
  'evaluation_not_draft',
  'evaluation_not_final',
  'evaluation_voided',
]);

const FORBIDDEN_CODES = new Set([
  'evaluation_author_only',
  'evaluation_read_only',
  'evaluation_tutor_only',
  'permission_denied',
]);

/** How the page shows a refused write (C8). */
export type Refusal =
  | { kind: 'fields'; errors: EvaluationErrors }
  | { kind: 'template' }
  | { kind: 'session' }
  | { kind: 'incomplete'; positions: number[] }
  | { kind: 'conflict' }
  | { kind: 'stale' }
  | { kind: 'state' }
  | { kind: 'forbidden' }
  | { kind: 'notFound' }
  | { kind: 'other' };

const SERVER_FIELD_MESSAGES: Record<string, string> = {
  score_out_of_range: `${V}.scoreRange`,
  score_precision: `${V}.scorePrecision`,
  decimal_invalid: `${V}.scoreInvalid`,
  score_required: `${V}.scoreRequired`,
  change_reason_required: `${V}.reasonRequired`,
  reason_required: `${V}.reasonRequired`,
  evaluated_on_in_future: `${V}.dateInFuture`,
  evaluated_on_invalid: `${V}.dateInvalid`,
  subject_required: `${V}.subjectRequired`,
  subject_unknown: `${V}.subjectUnknown`,
  subject_mismatch: `${V}.subjectMismatch`,
};

function fieldMessage(code: string, field: string): string {
  if (code === 'too_long') {
    return field === 'feedback'
      ? `${V}.feedbackTooLong`
      : field === 'change_reason'
        ? `${V}.reasonTooLong`
        : `${V}.commentTooLong`;
  }
  return SERVER_FIELD_MESSAGES[code] ?? `errors:${code}`;
}

/**
 * Sorts a refused write (C8). `positions` maps the index of the sent
 * `scores` array to its criterion position (the order sent).
 */
export function classifyRefusal(
  error: unknown,
  positions: readonly number[],
): Refusal {
  if (!(error instanceof ApiError)) {
    return { kind: 'other' };
  }
  if (error.status === 404) {
    return { kind: 'notFound' };
  }
  const codes = error.fieldErrors.map((item) => item.code);
  if (
    error.code === 'template_inactive' ||
    codes.includes('template_unknown')
  ) {
    return { kind: 'template' };
  }
  if (
    error.code === 'session_not_evaluable' ||
    codes.includes('class_session_invalid')
  ) {
    return { kind: 'session' };
  }
  if (error.code === 'evaluation_incomplete') {
    const raw = error.problem?.details?.missing_positions;
    const list = Array.isArray(raw)
      ? raw
      : typeof raw === 'string'
        ? [raw]
        : [];
    return {
      kind: 'incomplete',
      positions: list
        .filter((item) => /^\d{1,2}$/.test(item))
        .map((item) => Number(item)),
    };
  }
  if (error.code === 'version_conflict') {
    return { kind: 'conflict' };
  }
  if (codes.some((code) => STALE_CODES.has(code))) {
    return { kind: 'stale' };
  }
  if (STATE_CODES.has(error.code)) {
    return { kind: 'state' };
  }
  if (FORBIDDEN_CODES.has(error.code)) {
    return { kind: 'forbidden' };
  }
  const placed: EvaluationErrors = { scores: {} };
  let any = false;
  for (const item of error.fieldErrors) {
    if (!FIELD_CODES.has(item.code)) {
      continue;
    }
    const [where, field, index, leaf] = item.path;
    if (where !== 'body' || typeof field !== 'string') {
      continue;
    }
    const message = fieldMessage(item.code, field);
    if (field === 'scores' && typeof index === 'number') {
      const position = positions[index];
      if (position === undefined) {
        continue;
      }
      const entry = (placed.scores[position] ??= {});
      if (leaf === 'comment') {
        entry.comment = message;
      } else {
        entry.score = message;
      }
      any = true;
    } else if (field === 'evaluated_on') {
      placed.evaluatedOn = message;
      any = true;
    } else if (field === 'feedback') {
      placed.feedback = message;
      any = true;
    } else if (field === 'change_reason') {
      placed.changeReason = message;
      any = true;
    } else if (field === 'subject_id') {
      placed.subject = message;
      any = true;
    } else if (field === 'reason') {
      placed.changeReason = message;
      any = true;
    }
  }
  return any ? { kind: 'fields', errors: placed } : { kind: 'other' };
}

/** C7: the void reason's check (required, 1-500 once trimmed). */
export function voidReasonError(reason: string): EvaluationMessage | undefined {
  const length = reason.trim().length;
  if (length === 0) {
    return `${V}.reasonRequired`;
  }
  return length > REASON_MAX ? `${V}.reasonTooLong` : undefined;
}

/** C7: the server's `reason_required` on the void reason. */
export function serverVoidReasonError(
  error: unknown,
): EvaluationMessage | undefined {
  if (!(error instanceof ApiError)) {
    return undefined;
  }
  const found = error.fieldErrors.find(
    (item) => item.path[1] === 'reason' || item.code === 'reason_required',
  );
  if (!found) {
    return undefined;
  }
  return found.code === 'too_long'
    ? `${V}.reasonTooLong`
    : `${V}.reasonRequired`;
}

/** D1: a tutor's confirmed or completed entry offers "Academic history";
 * once it has started, "Evaluate" too. */
export function tutorAcademicLinks(
  item: { state: string; starts_at: string },
  now: number,
): { history: boolean; evaluate: boolean } {
  const open = item.state === 'confirmed' || item.state === 'completed';
  return {
    history: open,
    evaluate: open && Date.parse(item.starts_at) <= now,
  };
}
