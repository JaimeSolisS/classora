import type { components } from '@classora/api-client';
import { api, apiCall } from '@/app/api';
import { idempotencyHeaders } from '@/lib/idempotency';

// The payroll calls of #425, #444 and #454: the tutor list, one tutor's
// compensation, the settings, the pay rules, the unresolved work, the pay item
// types and a tutor's items are queries; adding and deleting a version, saving
// the settings and changing a type or an item are direct calls after which the
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
export type PayItemType = components['schemas']['PayItemTypeItem'];
export type PayItemTypeCreate = components['schemas']['PayItemTypeCreate'];
export type PayItemTypeUpdate = components['schemas']['PayItemTypeUpdate'];
export type PayItemDirection = components['schemas']['PayItemDirection'];
export type PayItem = components['schemas']['PayItemDetail'];
export type PayItemCreate = components['schemas']['PayItemCreate'];
export type PayItemRecurrence = components['schemas']['PayItemRecurrence'];
export type PayItemPage = components['schemas']['OffsetPage_PayItemDetail_'];

/** Rows per page of the tutor list (the API default). */
export const PAYROLL_TUTORS_PAGE_SIZE = 25;
/** Rows per "Load more" of a tutor's bonuses and deductions. */
export const PAY_ITEMS_PAGE_SIZE = 25;

/** Every cached payroll tutor query (the list pages and each tutor). */
export const PAYROLL_TUTORS_KEY = ['payroll', 'tutors'] as const;
export const PAYROLL_SETTINGS_KEY = ['payroll', 'settings'] as const;
export const PAY_RULES_KEY = ['payroll', 'pay-rules'] as const;
/** Every cached page of the unresolved work, whatever the filters. */
export const UNRESOLVED_KEY = ['payroll', 'unresolved'] as const;
export const PAY_ITEM_TYPES_KEY = ['payroll', 'pay-item-types'] as const;
/** Every tutor's items. Apart from the tutor queries: a change to an item
 * refreshes the items and nothing else (#454 B8). */
export const PAY_ITEMS_KEY = ['payroll', 'pay-items'] as const;
/** The section's anchor, which the Unresolved page links to (#454 C1). */
export const PAY_ITEMS_ANCHOR = 'pay-items';

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

export function payItemsKey(tutorUserId: string) {
  return [...PAY_ITEMS_KEY, tutorUserId] as const;
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

export function fetchPayItemTypes(signal: AbortSignal): Promise<PayItemType[]> {
  return apiCall(() => api.GET('/api/v1/payroll/pay-item-types', { signal }), {
    signal,
  });
}

export function createPayItemType(
  body: PayItemTypeCreate,
): Promise<PayItemType> {
  return apiCall(() => api.POST('/api/v1/payroll/pay-item-types', { body }));
}

export function updatePayItemType(
  typeId: string,
  body: PayItemTypeUpdate,
): Promise<PayItemType> {
  return apiCall(() =>
    api.PATCH('/api/v1/payroll/pay-item-types/{type_id}', {
      params: { path: { type_id: typeId } },
      body,
    }),
  );
}

export function fetchPayItems(
  tutorUserId: string,
  offset: number,
  signal: AbortSignal,
): Promise<PayItemPage> {
  return apiCall(
    () =>
      api.GET('/api/v1/payroll/tutors/{tutor_user_id}/pay-items', {
        params: {
          path: { tutor_user_id: tutorUserId },
          query: { limit: PAY_ITEMS_PAGE_SIZE, offset },
        },
        signal,
      }),
    { signal },
  );
}

/**
 * Adds an item (#454 B4). `idempotencyKey` comes from the dialog's
 * `IdempotentSubmission.keyFor(body)`, computed before this call so that a
 * retry after a lost response resends it and can't pay twice.
 */
export function createPayItem(
  tutorUserId: string,
  body: PayItemCreate,
  idempotencyKey: string,
): Promise<PayItem> {
  const headers = idempotencyHeaders(idempotencyKey);
  return apiCall(() =>
    api.POST('/api/v1/payroll/tutors/{tutor_user_id}/pay-items', {
      params: { path: { tutor_user_id: tutorUserId }, header: headers },
      body,
    }),
  );
}

export function endPayItem(
  tutorUserId: string,
  item: Pick<PayItem, 'id' | 'version'>,
  effectiveTo: string,
): Promise<PayItem> {
  return apiCall(() =>
    api.PATCH('/api/v1/payroll/tutors/{tutor_user_id}/pay-items/{item_id}', {
      params: { path: { tutor_user_id: tutorUserId, item_id: item.id } },
      body: { version: item.version, effective_to: effectiveTo },
    }),
  );
}

export async function deletePayItem(
  tutorUserId: string,
  itemId: string,
): Promise<void> {
  await apiCall(() =>
    api.DELETE('/api/v1/payroll/tutors/{tutor_user_id}/pay-items/{item_id}', {
      params: { path: { tutor_user_id: tutorUserId, item_id: itemId } },
    }),
  );
}

// ------------------------------------------------- payroll periods (#110b)

export type PayrollPeriod = components['schemas']['PayrollPeriodItem'];
export type PayrollPeriodPage =
  components['schemas']['OffsetPage_PayrollPeriodItem_'];
export type PayrollPeriodReview = components['schemas']['PayrollPeriodReview'];
export type PayrollPeriodDetail = components['schemas']['PayrollPeriodDetail'];
export type PayrollLine = components['schemas']['PayrollLineItem'];
export type PeriodStatus = components['schemas']['PeriodStatus'];
export type LineKind = components['schemas']['LineKind'];
export type PayrollPeriodTransition =
  components['schemas']['PayrollPeriodTransition'];
export type PayrollRecalculation =
  components['schemas']['PayrollRecalculation'];

/** Rows per page of the period list (the API default). */
export const PAYROLL_PERIODS_PAGE_SIZE = 25;

/** Every cached period query: the list pages and each review (#110b D15). */
export const PAYROLL_PERIODS_KEY = ['payroll', 'periods'] as const;

export interface PayrollPeriodListParams {
  status: PeriodStatus | null;
  /** A tutor's user id. */
  tutor: string | null;
  /** 1-based page number. */
  page: number;
}

export function periodsListKey(params: PayrollPeriodListParams) {
  return [...PAYROLL_PERIODS_KEY, 'list', params] as const;
}

export function periodReviewKey(periodId: string) {
  return [...PAYROLL_PERIODS_KEY, 'review', periodId] as const;
}

export function fetchPayrollPeriods(
  { status, tutor, page }: PayrollPeriodListParams,
  signal: AbortSignal,
): Promise<PayrollPeriodPage> {
  return apiCall(
    () =>
      api.GET('/api/v1/payroll/periods', {
        params: {
          query: {
            ...(status ? { status } : {}),
            ...(tutor ? { tutor_user_id: tutor } : {}),
            limit: PAYROLL_PERIODS_PAGE_SIZE,
            offset: (page - 1) * PAYROLL_PERIODS_PAGE_SIZE,
          },
        },
        signal,
      }),
    { signal },
  );
}

export function fetchPeriodReview(
  periodId: string,
  signal: AbortSignal,
): Promise<PayrollPeriodReview> {
  return apiCall(
    () =>
      api.GET('/api/v1/payroll/periods/{period_id}/lines', {
        params: { path: { period_id: periodId } },
        signal,
      }),
    { signal },
  );
}

export function recalculatePeriod(
  periodId: string,
): Promise<PayrollRecalculation> {
  return apiCall(() =>
    api.POST('/api/v1/payroll/periods/{period_id}/recalculate', {
      params: { path: { period_id: periodId } },
    }),
  );
}

export function preparePeriod(
  periodId: string,
): Promise<PayrollPeriodTransition> {
  return apiCall(() =>
    api.POST('/api/v1/payroll/periods/{period_id}/prepare', {
      params: { path: { period_id: periodId } },
    }),
  );
}

export function returnPeriod(
  periodId: string,
  reason: string,
): Promise<PayrollPeriodTransition> {
  return apiCall(() =>
    api.POST('/api/v1/payroll/periods/{period_id}/return', {
      params: { path: { period_id: periodId } },
      body: { reason },
    }),
  );
}

/**
 * Finalizes a prepared period (#110 E4, #110b D7-D9). `preparedAt` is the
 * `prepared_at` of the review the dialog showed. `idempotencyKey` is computed
 * before this call, so apiCall's retry after the password dialog and a retry
 * after a lost response resend it.
 */
export function finalizePeriod(
  periodId: string,
  preparedAt: string,
  idempotencyKey: string,
): Promise<PayrollPeriodTransition> {
  const headers = idempotencyHeaders(idempotencyKey);
  return apiCall(() =>
    api.POST('/api/v1/payroll/periods/{period_id}/finalize', {
      params: { path: { period_id: periodId }, header: headers },
      body: { prepared_at: preparedAt },
    }),
  );
}

// ------------------------------------- corrections after lock (M7-08b)

export type PayrollAdjustmentCreate =
  components['schemas']['PayrollAdjustmentCreate'];
export type PayrollAdjustmentPosted =
  components['schemas']['PayrollAdjustmentPosted'];
export type PayrollCarryForward = components['schemas']['PayrollCarryForward'];
export type PayrollAdjustmentKind =
  components['schemas']['app__modules__payroll__schemas__AdjustmentKind'];

/**
 * Posts a `manual` adjustment (M7-08c). `idempotencyKey` is computed before
 * this call, so apiCall's retry after the password dialog
 * (`finance.correction`) and a retry after a lost response resend it.
 */
export function postPayrollAdjustment(
  tutorUserId: string,
  body: PayrollAdjustmentCreate,
  idempotencyKey: string,
): Promise<PayrollAdjustmentPosted> {
  const headers = idempotencyHeaders(idempotencyKey);
  return apiCall(() =>
    api.POST('/api/v1/payroll/tutors/{tutor_user_id}/adjustments', {
      params: { path: { tutor_user_id: tutorUserId }, header: headers },
      body,
    }),
  );
}

/** Moves an ended open period's negative total forward (M7-08c E3). */
export function carryPeriodForward(
  periodId: string,
  idempotencyKey: string,
): Promise<PayrollCarryForward> {
  const headers = idempotencyHeaders(idempotencyKey);
  return apiCall(() =>
    api.POST('/api/v1/payroll/periods/{period_id}/carry-forward', {
      params: { path: { period_id: periodId }, header: headers },
    }),
  );
}
