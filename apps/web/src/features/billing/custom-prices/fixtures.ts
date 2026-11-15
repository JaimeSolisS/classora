import { FAMILY_LOPEZ, STUDENT_ANA } from '../discounts/fixtures';
import {
  PRICE_NOT_OFFERED,
  PRICE_NO_PRICE_YET,
  PRICE_WITH_NEXT,
} from '../pricing/fixtures';
import type { CustomPrice, CustomPricePage } from './api';

// Custom price fixtures (#367 G1, G2) for the tests and the stories. The
// center's "today" in them is 2026-10-20.

function catalogItem(
  item: typeof PRICE_WITH_NEXT,
): CustomPrice['service_price'] {
  return {
    id: item.id,
    class_type: item.class_type,
    pricing_model: item.pricing_model,
    subject: item.subject,
    education_level: item.education_level,
    duration_minutes: item.duration_minutes,
    cohort: item.cohort,
    group: item.group,
    current: item.current,
  };
}

/** Ana's individual sessions at 300.00, current with no end. */
export const CP_CURRENT: CustomPrice = {
  id: '80000000-0000-4000-8000-000000000001',
  service_price: catalogItem(PRICE_WITH_NEXT),
  student: { id: STUDENT_ANA.id, name: STUDENT_ANA.name },
  family: null,
  effective_from: '2026-09-01',
  expires_on: null,
  amount: '300.00',
  status: 'current',
  in_use: true,
  version: 2,
  created_at: '2026-08-20T16:00:00Z',
};

/** The López family's group fee from next month, not used yet. */
export const CP_UPCOMING: CustomPrice = {
  id: '80000000-0000-4000-8000-000000000002',
  service_price: catalogItem(PRICE_NOT_OFFERED),
  student: null,
  family: { id: FAMILY_LOPEZ.id, name: FAMILY_LOPEZ.name },
  effective_from: '2026-11-01',
  expires_on: '2026-12-31',
  amount: '1000.00',
  status: 'upcoming',
  in_use: false,
  version: 1,
  created_at: '2026-10-10T16:00:00Z',
};

/** Ana's old course fee: past. */
export const CP_PAST: CustomPrice = {
  id: '80000000-0000-4000-8000-000000000003',
  service_price: catalogItem(PRICE_NO_PRICE_YET),
  student: { id: STUDENT_ANA.id, name: STUDENT_ANA.name },
  family: null,
  effective_from: '2026-01-01',
  expires_on: '2026-06-30',
  amount: '0.00',
  status: 'past',
  in_use: true,
  version: 3,
  created_at: '2025-12-20T16:00:00Z',
};

export const CUSTOM_PRICES_PAGE: CustomPricePage = {
  items: [CP_CURRENT, CP_UPCOMING, CP_PAST],
  total: 3,
  limit: 25,
  offset: 0,
};

export const EMPTY_CUSTOM_PAGE: CustomPricePage = {
  items: [],
  total: 0,
  limit: 25,
  offset: 0,
};
