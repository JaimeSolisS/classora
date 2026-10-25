import type { components } from '@classora/api-client';
import { api, apiCall } from '@/app/api';
import { ACADEMICS_KEY } from '../keys';

// The evaluation template calls (#80 C1, #308 B): the list (filters by
// status, kind, subject and a name part; at most 200 rows, no pagination),
// one template, the create and the whole-template replace (with its version
// and is_active). evaluation.manage holders read every status; tutors only
// active templates (the evaluation form). The API decides.

export type EvaluationTemplate =
  components['schemas']['EvaluationTemplateView'];
export type EvaluationTemplateCreate =
  components['schemas']['EvaluationTemplateCreate'];
export type EvaluationTemplateReplace =
  components['schemas']['EvaluationTemplateReplace'];
export type EvaluationKind = components['schemas']['EvaluationKind'];
export type TemplateStatus = 'active' | 'inactive' | 'all';

export const EVALUATION_KINDS: readonly EvaluationKind[] = [
  'diagnostic',
  'ongoing',
];

export interface TemplateFilters {
  status: TemplateStatus;
  kind: EvaluationKind | null;
  subject: string | null;
  q: string;
}

export const TEMPLATES_KEY = [...ACADEMICS_KEY, 'templates'] as const;

export function templatesKey(filters: TemplateFilters) {
  return [...TEMPLATES_KEY, 'list', filters] as const;
}

export function templateKey(templateId: string) {
  return [...TEMPLATES_KEY, 'detail', templateId] as const;
}

export function fetchEvaluationTemplates(
  filters: TemplateFilters,
  signal?: AbortSignal,
): Promise<EvaluationTemplate[]> {
  return apiCall(
    () =>
      api.GET('/api/v1/evaluation-templates', {
        params: {
          query: {
            status: filters.status,
            ...(filters.kind ? { kind: filters.kind } : {}),
            ...(filters.subject ? { subject_id: filters.subject } : {}),
            ...(filters.q ? { q: filters.q } : {}),
          },
        },
        signal,
      }),
    { signal },
  ).then((list) => list.items);
}

export function fetchEvaluationTemplate(
  templateId: string,
  signal?: AbortSignal,
): Promise<EvaluationTemplate> {
  return apiCall(
    () =>
      api.GET('/api/v1/evaluation-templates/{template_id}', {
        params: { path: { template_id: templateId } },
        signal,
      }),
    { signal },
  );
}

export function createEvaluationTemplate(
  body: EvaluationTemplateCreate,
): Promise<EvaluationTemplate> {
  return apiCall(() => api.POST('/api/v1/evaluation-templates', { body }));
}

export function replaceEvaluationTemplate(
  templateId: string,
  body: EvaluationTemplateReplace,
): Promise<EvaluationTemplate> {
  return apiCall(() =>
    api.PUT('/api/v1/evaluation-templates/{template_id}', {
      params: { path: { template_id: templateId } },
      body,
    }),
  );
}

/** The editor of a template. */
export function templatePath(templateId: string): string {
  return `/academics/evaluation-templates/${templateId}`;
}
