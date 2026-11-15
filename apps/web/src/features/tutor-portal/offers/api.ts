import type { components } from '@classora/api-client';
import { api, apiCall } from '@/app/api';

// The tutor's offers (#52 H1): the list of their own offers (the API
// filters by record policy), accept and decline.

export type AssignmentOfferItem = components['schemas']['AssignmentOfferItem'];
export type AssignmentOfferPage = components['schemas']['AssignmentOfferPage'];
export type OfferSession = components['schemas']['OfferSession'];
export type OfferTab = 'pending' | 'answered';

/** The most offers a tab loads (one page). */
export const OFFERS_LIMIT = 100;

export const OFFERS_KEY = ['tutor-portal', 'offers'] as const;

export function fetchOffers(
  status: OfferTab,
  signal?: AbortSignal,
): Promise<AssignmentOfferPage> {
  return apiCall(
    () =>
      api.GET('/api/v1/assignment-offers', {
        params: { query: { status, limit: OFFERS_LIMIT } },
        signal,
      }),
    { signal },
  );
}

export function acceptOffer(offerId: string): Promise<AssignmentOfferItem> {
  return apiCall(() =>
    api.POST('/api/v1/assignment-offers/{offer_id}/accept', {
      params: { path: { offer_id: offerId } },
    }),
  );
}

export function declineOffer(
  offerId: string,
  note: string,
): Promise<AssignmentOfferItem> {
  const trimmed = note.trim();
  return apiCall(() =>
    api.POST('/api/v1/assignment-offers/{offer_id}/decline', {
      params: { path: { offer_id: offerId } },
      body: { note: trimmed || null },
    }),
  );
}
