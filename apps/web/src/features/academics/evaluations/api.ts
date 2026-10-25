import type { components } from '@classora/api-client';
import { api, apiCall } from '@/app/api';
import { ACADEMICS_KEY } from '../keys';

// The evaluation calls (#80 D, #308 C): a class's evaluation context (its
// current tutor only), the create (a draft), one evaluation (the working
// view with its can_* flags), the edit and amendment (PATCH with the
// version), finalize, void and delete (drafts). The API's record policies
// decide; the pages render by the can_* flags.

export type Evaluation = components['schemas']['EvaluationView'];
export type EvaluationCreate = components['schemas']['EvaluationCreate'];
export type EvaluationPatch = components['schemas']['EvaluationPatch'];
export type EvaluationContext = components['schemas']['EvaluationContext'];
export type EvaluationScoreInput =
  components['schemas']['EvaluationScoreInput'];

export const EVALUATIONS_KEY = [...ACADEMICS_KEY, 'evaluations'] as const;

export function evaluationKey(evaluationId: string) {
  return [...EVALUATIONS_KEY, 'detail', evaluationId] as const;
}

export function evaluationContextKey(sessionId: string) {
  return [...EVALUATIONS_KEY, 'context', sessionId] as const;
}

export function fetchEvaluationContext(
  sessionId: string,
  signal?: AbortSignal,
): Promise<EvaluationContext> {
  return apiCall(
    () =>
      api.GET('/api/v1/class-sessions/{class_session_id}/evaluation-context', {
        params: { path: { class_session_id: sessionId } },
        signal,
      }),
    { signal },
  );
}

export function fetchEvaluation(
  evaluationId: string,
  signal?: AbortSignal,
): Promise<Evaluation> {
  return apiCall(
    () =>
      api.GET('/api/v1/evaluations/{evaluation_id}', {
        params: { path: { evaluation_id: evaluationId } },
        signal,
      }),
    { signal },
  );
}

export function createEvaluation(body: EvaluationCreate): Promise<Evaluation> {
  return apiCall(() => api.POST('/api/v1/evaluations', { body }));
}

export function patchEvaluation(
  evaluationId: string,
  body: EvaluationPatch,
): Promise<Evaluation> {
  return apiCall(() =>
    api.PATCH('/api/v1/evaluations/{evaluation_id}', {
      params: { path: { evaluation_id: evaluationId } },
      body,
    }),
  );
}

export function finalizeEvaluation(
  evaluation: Pick<Evaluation, 'id' | 'version'>,
): Promise<Evaluation> {
  return apiCall(() =>
    api.POST('/api/v1/evaluations/{evaluation_id}/finalize', {
      params: { path: { evaluation_id: evaluation.id } },
      body: { version: evaluation.version },
    }),
  );
}

export function voidEvaluation(
  evaluation: Pick<Evaluation, 'id' | 'version'>,
  reason: string,
): Promise<Evaluation> {
  return apiCall(() =>
    api.POST('/api/v1/evaluations/{evaluation_id}/void', {
      params: { path: { evaluation_id: evaluation.id } },
      body: { version: evaluation.version, reason },
    }),
  );
}

export async function deleteEvaluation(
  evaluation: Pick<Evaluation, 'id' | 'version'>,
): Promise<void> {
  await apiCall(() =>
    api.DELETE('/api/v1/evaluations/{evaluation_id}', {
      params: {
        path: { evaluation_id: evaluation.id },
        query: { version: evaluation.version },
      },
    }),
  );
}

/** One evaluation's page. */
export function evaluationPath(evaluationId: string): string {
  return `/evaluations/${evaluationId}`;
}

/** The new-evaluation form, linked to a class when `sessionId` is given. */
export function newEvaluationPath(
  studentId: string,
  sessionId?: string,
): string {
  const base = `/students/${studentId}/evaluations/new`;
  return sessionId
    ? `${base}?sessionId=${encodeURIComponent(sessionId)}`
    : base;
}

/** A student's academic history page (staff and tutors). */
export function studentHistoryPath(
  studentId: string,
  tab?: 'evaluations',
): string {
  const base = `/students/${studentId}/history`;
  return tab ? `${base}?tab=${tab}` : base;
}
