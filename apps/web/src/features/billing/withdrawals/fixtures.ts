import {
  ACTIVE_ANA as GROUP_ANA,
  WITHDRAWN_CARLA,
} from '@/features/groups/fixtures';
import { ENROLLED_ANA as COHORT_ANA } from '@/features/courses/cohorts/fixtures';
import { FAMILY_ID, SUMMARY } from '../accounts/fixtures';
import { FAMILY_LOPEZ, PACKAGE_ACTIVE } from '../packages/fixtures';
import type {
  PreviewBlocking,
  WithdrawalConfirmed,
  WithdrawalDetail,
  WithdrawalItem,
  WithdrawalPage,
  WithdrawalPreview,
} from './api';

// Withdrawal fixtures (#387 D1, D2) for the tests and the stories. The
// center's "today" is 2026-10-20 (the accounts fixtures).

const FINGERPRINT = 'a'.repeat(64);
const FRESH_FINGERPRINT = 'b'.repeat(64);
export const PURCHASE_CHARGE_ID = PACKAGE_ACTIVE.purchase_charge_id!;
export const FEE_CHARGE_ID = 'e1000000-0000-4000-8000-000000000001';
export const FUTURE_SESSION_ID = 'f1000000-0000-4000-8000-000000000001';
export const CLOSEOUT_SESSION_ID = 'f1000000-0000-4000-8000-000000000002';
export const WITHDRAWAL_ID = '56000000-0000-4000-8000-000000000001';

const ZERO_TOTALS = {
  credit: '0.00',
  reduces_debt: '0.00',
  refundable_cash: '0.00',
  automatic_credits: '0.00',
  account_credit_after_credit: '0.00',
};

/** 8 of 10 sessions left, unpaid: $1,440.00 credit, nothing to refund. */
export const PACKAGE_PREVIEW: WithdrawalPreview = {
  subject: { kind: 'package', id: PACKAGE_ACTIVE.id },
  student: { id: PACKAGE_ACTIVE.student.id, name: PACKAGE_ACTIVE.student.name },
  family: FAMILY_LOPEZ,
  billing_account_id: '54000000-0000-4000-8000-000000000002',
  enrollment_status: null,
  enrollment_version: null,
  enrolled_at: null,
  at: '2026-10-20T18:00:00Z',
  sessions: null,
  units: { unit: 'sessions', granted: 10, consumed: 2, remaining: 8 },
  lines: [
    {
      charge_id: PURCHASE_CHARGE_ID,
      kind: 'package_purchase',
      service_date: '2026-10-01',
      service_period_end: null,
      net: '1800.00',
      allocated: '0.00',
      basis: { consumed: 2, unused: 8, denominator: 10 },
      policy: 'pro_rata',
      computed_credit: '1440.00',
      posting: 'posted',
    },
  ],
  automatic_credits: [],
  totals: {
    ...ZERO_TOTALS,
    credit: '1440.00',
    reduces_debt: '1440.00',
  },
  warnings: [
    {
      code: 'future_covered_sessions',
      count: 1,
      class_session_ids: [FUTURE_SESSION_ID],
    },
  ],
  blocking: [],
  fingerprint: FINGERPRINT,
};

/** The same package fully paid: the credit frees $1,440.00 to refund. */
export const PACKAGE_PREVIEW_PAID: WithdrawalPreview = {
  ...PACKAGE_PREVIEW,
  lines: [{ ...PACKAGE_PREVIEW.lines[0]!, allocated: '1800.00' }],
  totals: {
    ...ZERO_TOTALS,
    credit: '1440.00',
    reduces_debt: '0.00',
    refundable_cash: '1440.00',
    account_credit_after_credit: '1440.00',
  },
  warnings: [],
};

/** Minutes: 12 h 30 min left of 20 h. */
export const PACKAGE_PREVIEW_HOURS: WithdrawalPreview = {
  ...PACKAGE_PREVIEW,
  units: { unit: 'minutes', granted: 1200, consumed: 450, remaining: 750 },
  lines: [
    {
      ...PACKAGE_PREVIEW.lines[0]!,
      net: '3000.00',
      basis: { consumed: 450, unused: 750, denominator: 1200 },
      computed_credit: '1875.00',
    },
  ],
  totals: { ...ZERO_TOTALS, credit: '1875.00', reduces_debt: '1875.00' },
  warnings: [],
};

/** What a stale confirm answers: one more session used meanwhile. */
export const PACKAGE_PREVIEW_FRESH: WithdrawalPreview = {
  ...PACKAGE_PREVIEW,
  at: '2026-10-20T18:01:00Z',
  units: { unit: 'sessions', granted: 10, consumed: 3, remaining: 7 },
  lines: [
    {
      ...PACKAGE_PREVIEW.lines[0]!,
      basis: { consumed: 3, unused: 7, denominator: 10 },
      computed_credit: '1260.00',
    },
  ],
  totals: { ...ZERO_TOTALS, credit: '1260.00', reduces_debt: '1260.00' },
  warnings: [],
  fingerprint: FRESH_FINGERPRINT,
};

/**
 * An active group enrollment: a paid fee credited pro rata (5 of 8
 * sessions unused), a due tuition period posted now (full credit) and two
 * automatic session credits of $150.00.
 */
export const GROUP_PREVIEW: WithdrawalPreview = {
  subject: { kind: 'group_enrollment', id: GROUP_ANA.id },
  student: { id: GROUP_ANA.student.id, name: GROUP_ANA.student.display_name },
  family: { id: FAMILY_ID, name: 'Familia López' },
  billing_account_id: SUMMARY.account_id,
  enrollment_status: 'active',
  enrollment_version: GROUP_ANA.version,
  enrolled_at: GROUP_ANA.enrolled_at,
  at: '2026-10-20T18:00:00Z',
  sessions: {
    consumed: 3,
    unused: 5,
    consumed_minutes: 270,
    unused_minutes: 450,
  },
  units: null,
  lines: [
    {
      charge_id: FEE_CHARGE_ID,
      kind: 'group_enrollment',
      service_date: '2026-09-01',
      service_period_end: null,
      net: '500.00',
      allocated: '500.00',
      basis: { consumed: 3, unused: 5, denominator: 8 },
      policy: 'pro_rata',
      computed_credit: '312.50',
      posting: 'posted',
    },
    {
      charge_id: null,
      kind: 'tuition_period',
      service_date: '2026-10-01',
      service_period_end: '2026-10-31',
      net: '800.00',
      allocated: '0.00',
      basis: { consumed: 0, unused: 4, denominator: 4 },
      policy: 'nothing_delivered',
      computed_credit: '800.00',
      posting: 'now',
    },
  ],
  automatic_credits: [
    {
      charge_id: 'e1000000-0000-4000-8000-000000000011',
      class_session_id: 'f1000000-0000-4000-8000-000000000011',
      amount: '150.00',
    },
    {
      charge_id: 'e1000000-0000-4000-8000-000000000012',
      class_session_id: 'f1000000-0000-4000-8000-000000000012',
      amount: '150.00',
    },
  ],
  totals: {
    credit: '1112.50',
    reduces_debt: '800.00',
    refundable_cash: '612.50',
    automatic_credits: '300.00',
    account_credit_after_credit: '912.50',
  },
  warnings: [{ code: 'tuition_posted_now', count: 1, class_session_ids: [] }],
  blocking: [],
  fingerprint: FINGERPRINT,
};

/** A group enrollment withdrawn through M4, settled as of then (S3). */
export const GROUP_SETTLE_PREVIEW: WithdrawalPreview = {
  ...GROUP_PREVIEW,
  subject: { kind: 'group_enrollment', id: WITHDRAWN_CARLA.id },
  student: {
    id: WITHDRAWN_CARLA.student.id,
    name: WITHDRAWN_CARLA.student.display_name,
  },
  enrollment_status: 'withdrawn',
  enrollment_version: null,
  at: WITHDRAWN_CARLA.withdrawn_at!,
  lines: [GROUP_PREVIEW.lines[0]!],
  automatic_credits: [],
  totals: {
    credit: '312.50',
    reduces_debt: '0.00',
    refundable_cash: '312.50',
    automatic_credits: '0.00',
    account_credit_after_credit: '612.50',
  },
  warnings: [],
};

/** An active cohort enrollment: its course fee, nothing delivered yet. */
export const COHORT_PREVIEW: WithdrawalPreview = {
  ...GROUP_PREVIEW,
  subject: { kind: 'cohort_enrollment', id: COHORT_ANA.id },
  student: { id: COHORT_ANA.student.id, name: COHORT_ANA.student.display_name },
  enrollment_version: COHORT_ANA.version,
  enrolled_at: COHORT_ANA.enrolled_at,
  sessions: {
    consumed: 0,
    unused: 10,
    consumed_minutes: 0,
    unused_minutes: 900,
  },
  lines: [
    {
      charge_id: 'e1000000-0000-4000-8000-000000000021',
      kind: 'cohort_enrollment',
      service_date: '2026-10-01',
      service_period_end: null,
      net: '2400.00',
      allocated: '1000.00',
      basis: { consumed: 0, unused: 10, denominator: 10 },
      policy: 'nothing_delivered',
      computed_credit: '2400.00',
      posting: 'posted',
    },
  ],
  automatic_credits: [],
  totals: {
    credit: '2400.00',
    reduces_debt: '1400.00',
    refundable_cash: '1000.00',
    automatic_credits: '0.00',
    account_credit_after_credit: '1300.00',
  },
  warnings: [],
};

const BLOCKING_DETAILS: Record<PreviewBlocking['code'], PreviewBlocking> = {
  withdrawal_exists: {
    code: 'withdrawal_exists',
    details: { withdrawal_id: WITHDRAWAL_ID },
  },
  withdrawal_closeout_pending: {
    code: 'withdrawal_closeout_pending',
    details: { class_session_ids: [CLOSEOUT_SESSION_ID] },
  },
  package_not_withdrawable: {
    code: 'package_not_withdrawable',
    details: { reason: 'expired' },
  },
  student_has_no_family: { code: 'student_has_no_family', details: {} },
  withdrawal_spans_accounts: {
    code: 'withdrawal_spans_accounts',
    details: { billing_account_ids: ['a', 'b'] },
  },
};

/** The package preview blocked by `code`. */
export function blockedPreview(
  code: PreviewBlocking['code'],
): WithdrawalPreview {
  return {
    ...PACKAGE_PREVIEW,
    ...(code === 'student_has_no_family'
      ? { family: null, billing_account_id: null }
      : {}),
    blocking: [BLOCKING_DETAILS[code]],
  };
}

export const WITHDRAWAL_ITEM: WithdrawalItem = {
  id: WITHDRAWAL_ID,
  subject: { kind: 'package', id: PACKAGE_ACTIVE.id },
  student_id: SUMMARY.students[0]!.student.id,
  family_id: FAMILY_ID,
  billing_account_id: SUMMARY.account_id!,
  outcome: 'account_credit',
  amount: '1440.00',
  refund_amount: '0.00',
  withdrawn_at: '2026-10-20T18:00:00Z',
  reason: 'La familia se muda.',
  confirmed_by_user_id: '00000000-0000-4000-8000-000000000001',
  created_at: '2026-10-20T18:00:30Z',
  adjustment_ids: ['57000000-0000-4000-8000-000000000001'],
  refund_ids: [],
  automatic_credit_adjustment_ids: [],
};

export const WITHDRAWAL_DETAIL: WithdrawalDetail = {
  ...WITHDRAWAL_ITEM,
  preview: {
    ...PACKAGE_PREVIEW,
    student: { id: WITHDRAWAL_ITEM.student_id, name: 'Ana López' },
    outcome: 'account_credit',
    overrides: [],
    applied: [{ charge_id: PURCHASE_CHARGE_ID, applied_credit: '1440.00' }],
  },
};

/** A refunded group withdrawal by someone else. */
export const WITHDRAWAL_REFUND_ITEM: WithdrawalItem = {
  ...WITHDRAWAL_ITEM,
  id: '56000000-0000-4000-8000-000000000002',
  subject: { kind: 'group_enrollment', id: GROUP_ANA.id },
  outcome: 'refund',
  amount: '312.50',
  refund_amount: '612.50',
  withdrawn_at: '2026-10-10T18:00:00Z',
  confirmed_by_user_id: '00000000-0000-4000-8000-0000000000ff',
  refund_ids: ['58000000-0000-4000-8000-000000000001'],
  automatic_credit_adjustment_ids: [
    '57000000-0000-4000-8000-000000000011',
    '57000000-0000-4000-8000-000000000012',
  ],
};

export const WITHDRAWAL_REFUND_DETAIL: WithdrawalDetail = {
  ...WITHDRAWAL_REFUND_ITEM,
  preview: {
    ...GROUP_PREVIEW,
    outcome: 'refund',
    applied: [
      { charge_id: FEE_CHARGE_ID, applied_credit: '312.50' },
      {
        charge_id: 'e1000000-0000-4000-8000-000000000031',
        applied_credit: '0.00',
      },
    ],
  },
};

export const WITHDRAWALS_PAGE: WithdrawalPage = {
  items: [WITHDRAWAL_ITEM, WITHDRAWAL_REFUND_ITEM],
  total: 2,
  limit: 25,
  offset: 0,
};

export const EMPTY_WITHDRAWALS_PAGE: WithdrawalPage = {
  items: [],
  total: 0,
  limit: 25,
  offset: 0,
};

export const PACKAGE_CONFIRMED: WithdrawalConfirmed = {
  withdrawal: WITHDRAWAL_DETAIL,
  package_refund_transaction_id: 'a1000000-0000-4000-8000-000000000009',
  account_credit_after: '0.00',
  account: SUMMARY,
};

export const GROUP_CONFIRMED: WithdrawalConfirmed = {
  withdrawal: {
    ...WITHDRAWAL_REFUND_DETAIL,
    outcome: 'account_credit',
    amount: '1112.50',
    refund_amount: '0.00',
  },
  package_refund_transaction_id: null,
  account_credit_after: '912.50',
  account: SUMMARY,
};
