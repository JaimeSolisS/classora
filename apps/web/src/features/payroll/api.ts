import type { components } from '@classora/api-client';
import { api, apiCall } from '@/app/api';

// The payroll calls of #425: the tutor list, one tutor's compensation and the
// settings are queries; adding and deleting a version and saving the settings
// are direct calls after which the pages invalidate the queries. Every amount
// stays the API's decimal string.

export type PayrollTutor = components['schemas']['PayrollTutorItem'];
export type PayrollTutorPage =
  components['schemas']['OffsetPage_PayrollTutorItem_'];
export type TutorCompensation = components['schemas']['TutorCompensation'];
export type PayTerms = components['schemas']['PayTermsItem'];
export type PayTermsCreate = components['schemas']['PayTermsCreate'];
export type CompensationRate = components['schemas']['CompensationRateItem'];
export type CompensationRateCreate =
  components['schemas']['CompensationRateCreate'];
export type CompensationStatus = components['schemas']['CompensationStatus'];
export type ClassKind = components['schemas']['ClassKind'];
export type RateModel = components['schemas']['RateModel'];
export type PayFrequency = components['schemas']['PayFrequency'];
export type ProrationBasis = components['schemas']['ProrationBasis'];
export type PayrollSettings = components['schemas']['PayrollSettingsDetail'];
export type PayrollSettingsUpdate =
  components['schemas']['PayrollSettingsUpdate'];
export type UserState = components['schemas']['UserState'];

/** Rows per page of the tutor list (the API default). */
export const PAYROLL_TUTORS_PAGE_SIZE = 25;

/** Every cached payroll tutor query (the list pages and each tutor). */
export const PAYROLL_TUTORS_KEY = ['payroll', 'tutors'] as const;
export const PAYROLL_SETTINGS_KEY = ['payroll', 'settings'] as const;

export interface PayrollTutorListParams {
  q: string;
  /** A user state, or `all` for every state. */
  state: UserState | 'all';
  /** 1-based page number. */
  page: number;
}

export function payrollTutorsListKey(params: PayrollTutorListParams) {
  return [...PAYROLL_TUTORS_KEY, 'list', params] as const;
}

export function compensationKey(tutorUserId: string) {
  return [...PAYROLL_TUTORS_KEY, 'compensation', tutorUserId] as const;
}

export function fetchPayrollTutors(
  { q, state, page }: PayrollTutorListParams,
  signal: AbortSignal,
): Promise<PayrollTutorPage> {
  return apiCall(
    () =>
      api.GET('/api/v1/payroll/tutors', {
        params: {
          query: {
            ...(q ? { q } : {}),
            ...(state === 'all' ? {} : { state }),
            limit: PAYROLL_TUTORS_PAGE_SIZE,
            offset: (page - 1) * PAYROLL_TUTORS_PAGE_SIZE,
          },
        },
        signal,
      }),
    { signal },
  );
}

export function fetchCompensation(
  tutorUserId: string,
  signal: AbortSignal,
): Promise<TutorCompensation> {
  return apiCall(
    () =>
      api.GET('/api/v1/payroll/tutors/{tutor_user_id}/compensation', {
        params: { path: { tutor_user_id: tutorUserId } },
        signal,
      }),
    { signal },
  );
}

export function createPayTerms(
  tutorUserId: string,
  body: PayTermsCreate,
): Promise<PayTerms> {
  return apiCall(() =>
    api.POST('/api/v1/payroll/tutors/{tutor_user_id}/pay-terms', {
      params: { path: { tutor_user_id: tutorUserId } },
      body,
    }),
  );
}

export function createRate(
  tutorUserId: string,
  body: CompensationRateCreate,
): Promise<CompensationRate> {
  return apiCall(() =>
    api.POST('/api/v1/payroll/tutors/{tutor_user_id}/rates', {
      params: { path: { tutor_user_id: tutorUserId } },
      body,
    }),
  );
}

export async function deletePayTerms(
  tutorUserId: string,
  termsId: string,
): Promise<void> {
  await apiCall(() =>
    api.DELETE('/api/v1/payroll/tutors/{tutor_user_id}/pay-terms/{terms_id}', {
      params: { path: { tutor_user_id: tutorUserId, terms_id: termsId } },
    }),
  );
}

export async function deleteRate(
  tutorUserId: string,
  rateId: string,
): Promise<void> {
  await apiCall(() =>
    api.DELETE('/api/v1/payroll/tutors/{tutor_user_id}/rates/{rate_id}', {
      params: { path: { tutor_user_id: tutorUserId, rate_id: rateId } },
    }),
  );
}

export function fetchPayrollSettings(
  signal: AbortSignal,
): Promise<PayrollSettings> {
  return apiCall(() => api.GET('/api/v1/payroll/settings', { signal }), {
    signal,
  });
}

export function updatePayrollSettings(
  body: PayrollSettingsUpdate,
): Promise<PayrollSettings> {
  return apiCall(() => api.PATCH('/api/v1/payroll/settings', { body }));
}
