import type { components } from '@classora/api-client';
import { api, apiCall } from '@/app/api';

// The course template calls (#64): the list and one template are queries;
// create, replace (the whole document with `version`) and the status PATCH
// are direct calls after which the pages refresh the template queries.

export type CourseTemplate = components['schemas']['CourseTemplateDetail'];
export type CourseTemplateSummary =
  components['schemas']['CourseTemplateSummary'];
export type CourseTemplatePage =
  components['schemas']['OffsetPage_CourseTemplateSummary_'];
export type CourseTemplateWrite = components['schemas']['CourseTemplateWrite'];
export type CourseTemplateReplace =
  components['schemas']['CourseTemplateReplace'];
export type CourseTemplateStatus =
  components['schemas']['CourseTemplateStatus'];

/** A subject the editor offers (an active subject of any level). */
export interface SubjectOption {
  id: string;
  name: string;
  levelName: string;
}

/** Rows per page of the list (the API default). */
export const TEMPLATES_PAGE_SIZE = 25;

/** Every cached course template query. */
export const TEMPLATES_QUERY_KEY = ['courses', 'templates'] as const;

export function templatesListKey(status: CourseTemplateStatus, page: number) {
  return [...TEMPLATES_QUERY_KEY, 'list', status, page] as const;
}

export function templateKey(templateId: string) {
  return [...TEMPLATES_QUERY_KEY, 'detail', templateId] as const;
}

export const SUBJECT_OPTIONS_KEY = [
  'catalog',
  'subjects',
  'course-options',
] as const;

export function fetchTemplates(
  status: CourseTemplateStatus,
  page: number,
  signal: AbortSignal,
): Promise<CourseTemplatePage> {
  return apiCall(
    () =>
      api.GET('/api/v1/course-templates', {
        params: {
          query: {
            status,
            limit: TEMPLATES_PAGE_SIZE,
            offset: (page - 1) * TEMPLATES_PAGE_SIZE,
          },
        },
        signal,
      }),
    { signal },
  );
}

export function fetchTemplate(
  templateId: string,
  signal: AbortSignal,
): Promise<CourseTemplate> {
  return apiCall(
    () =>
      api.GET('/api/v1/course-templates/{template_id}', {
        params: { path: { template_id: templateId } },
        signal,
      }),
    { signal },
  );
}

export function createTemplate(
  body: CourseTemplateWrite,
): Promise<CourseTemplate> {
  return apiCall(() => api.POST('/api/v1/course-templates', { body }));
}

export function replaceTemplate(
  templateId: string,
  body: CourseTemplateReplace,
): Promise<CourseTemplate> {
  return apiCall(() =>
    api.PUT('/api/v1/course-templates/{template_id}', {
      params: { path: { template_id: templateId } },
      body,
    }),
  );
}

export function setTemplateActive(
  templateId: string,
  version: number,
  isActive: boolean,
): Promise<CourseTemplate> {
  return apiCall(() =>
    api.PATCH('/api/v1/course-templates/{template_id}', {
      params: { path: { template_id: templateId } },
      body: { version, is_active: isActive },
    }),
  );
}

/** The active subjects of every level (up to 100), in catalog order. */
export async function fetchSubjectOptions(
  signal: AbortSignal,
): Promise<SubjectOption[]> {
  const page = await apiCall(
    () =>
      api.GET('/api/v1/subjects', {
        params: { query: { status: 'active', limit: 100 } },
        signal,
      }),
    { signal },
  );
  return page.items
    .filter((item) => item.is_active)
    .map((item) => ({
      id: item.id,
      name: item.name,
      levelName: item.education_level_name,
    }));
}
