import type { components } from '@classora/api-client';
import { api, apiCall } from '@/app/api';

// The payroll calls of #425 and #444: the tutor list, one tutor's compensation,
// the settings, the pay rules and the unresolved work are queries; adding and
// deleting a version and saving the settings are direct calls after which the
// pages invalidate the queries. Every amount stays the API's decimal string.

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
export type PayRule = components['schemas']['SessionPayRuleItem'];
export type PayRuleCreate = components['schemas']['SessionPayRuleCreate'];
export type PayRuleOverview = components['schemas']['SessionPayRuleOverview'];
export type PayRuleEventOverview =
  components['schemas']['SessionPayRuleEventOverview'];
export type PayRuleEvent = components['schemas']['PayRuleEvent'];
export type PayRuleMode = components['schemas']['PayRuleMode'];
export type UnresolvedItem = components['schemas']['UnresolvedWorkItem'];
export type UnresolvedPage =
  components['schemas']['CursorPage_UnresolvedWorkItem_'];
export type UnresolvedReason = components['schemas']['UnresolvedReason'];

/** Rows per page of the tutor list (the API default). */
export const PAYROLL_TUTORS_PAGE_SIZE = 25;

/** Every cached payroll tutor query (the list pages and each tutor). */
export const PAYROLL_TUTORS_KEY = ['payroll', 'tutors'] as const;
export const PAYROLL_SETTINGS_KEY = ['payroll', 'settings'] as const;
export const PAY_RULES_KEY = ['payroll', 'pay-rules'] as const;
/** Every cached page of the unresolved work, whatever the filters. */
export const UNRESOLVED_KEY = ['payroll', 'unresolved'] as const;

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

export function fetchPayRules(signal: AbortSignal): Promise<PayRuleOverview> {
  return apiCall(() => api.GET('/api/v1/payroll/pay-rules', { signal }), {
    signal,
  });
}

export function createPayRule(body: PayRuleCreate): Promise<PayRule> {
  return apiCall(() => api.POST('/api/v1/payroll/pay-rules', { body }));
}

export async function deletePayRule(ruleId: string): Promise<void> {
  await apiCall(() =>
    api.DELETE('/api/v1/payroll/pay-rules/{rule_id}', {
      params: { path: { rule_id: ruleId } },
    }),
  );
}

export interface UnresolvedFilters {
  tutor: string | null;
  reason: UnresolvedReason | null;
}

export function unresolvedKey(filters: UnresolvedFilters) {
  return [...UNRESOLVED_KEY, filters] as const;
}

export function fetchUnresolved(
  { tutor, reason }: UnresolvedFilters,
  cursor: string | null,
  signal: AbortSignal,
): Promise<UnresolvedPage> {
  return apiCall(
    () =>
      api.GET('/api/v1/payroll/unresolved-work', {
        params: {
          query: {
            ...(tutor ? { tutor_user_id: tutor } : {}),
            ...(reason ? { reason } : {}),
            ...(cursor ? { cursor } : {}),
          },
        },
        signal,
      }),
    { signal },
  );
}

/** The tutor filter's picker: the Payroll list, in every state (#444). */
export async function searchPayrollTutors(
  q: string,
  signal: AbortSignal,
): Promise<PayrollTutor[]> {
  const page = await fetchPayrollTutors({ q, state: 'all', page: 1 }, signal);
  return page.items;
}
