import type { components } from '@classora/api-client';
import { api, apiCall } from '@/app/api';
import { accountKey } from '../accounts/api';

// The due-date calls of a family account (#97, #382): the family's offset
// (`PATCH …/billing-account`), a charge's due-date change and its history
// (`POST`/`GET …/charges/{charge_id}/due-date-changes`). Neither write
// moves money: no `Idempotency-Key`, no reauthentication. The history sits
// under the family's `accountKey`, so a write's invalidation reaches it.

export type BillingAccountUpdate =
  components['schemas']['BillingAccountUpdate'];
export type BillingAccountSettings =
  components['schemas']['BillingAccountSettings'];
export type DueDateChangeCreate = components['schemas']['DueDateChangeCreate'];
export type DueDateChange = components['schemas']['DueDateChangeItem'];
export type DueDateChangeRecorded =
  components['schemas']['DueDateChangeRecorded'];

export function dueDateChangesKey(familyId: string, chargeId: string) {
  return [...accountKey(familyId), 'due-date-changes', chargeId] as const;
}

/** A charge's due-date changes, oldest first (staff only). */
export function fetchDueDateChanges(
  familyId: string,
  chargeId: string,
  signal: AbortSignal,
): Promise<DueDateChange[]> {
  return apiCall(
    () =>
      api.GET(
        '/api/v1/families/{family_id}/billing-account/charges/{charge_id}/due-date-changes',
        {
          params: { path: { family_id: familyId, charge_id: chargeId } },
          signal,
        },
      ),
    { signal },
  );
}

/** Sets the family's due-date offset (`version: null` creates the account). */
export function updateDueOffset(
  familyId: string,
  body: BillingAccountUpdate,
): Promise<BillingAccountSettings> {
  return apiCall(() =>
    api.PATCH('/api/v1/families/{family_id}/billing-account', {
      body,
      params: { path: { family_id: familyId } },
    }),
  );
}

/** Changes one charge's due date, with a reason. */
export function recordDueDateChange(
  familyId: string,
  chargeId: string,
  body: DueDateChangeCreate,
): Promise<DueDateChangeRecorded> {
  return apiCall(() =>
    api.POST(
      '/api/v1/families/{family_id}/billing-account/charges/{charge_id}/due-date-changes',
      {
        body,
        params: { path: { family_id: familyId, charge_id: chargeId } },
      },
    ),
  );
}
