import type { FamilyListPage } from '@/features/families/api';
import type {
  AccountSummary,
  ChargeItem,
  ChargePage,
  LedgerEntry,
  LedgerPage,
  PaymentDetail,
  PaymentItem,
  PaymentMethod,
  PaymentPage,
  PaymentRecorded,
} from './api';

// Family account fixtures (#379 G1, G2) for the tests and the stories. The
// center's "today" in them is 2026-10-20 (America/Mexico_City).

export const TODAY = '2026-10-20';

export const FAMILY_ID = '51000000-0000-4000-8000-000000000001';
const FAMILY_EMPTY_ID = '51000000-0000-4000-8000-000000000002';
export const ANA = {
  id: '52000000-0000-4000-8000-000000000001',
  name: 'Ana López',
};
export const LUIS = {
  id: '52000000-0000-4000-8000-000000000002',
  name: 'Luis López',
};

export const FAMILIES_PAGE: FamilyListPage = {
  items: [
    { id: FAMILY_ID, name: 'Familia López', is_active: true, student_count: 2 },
    {
      id: FAMILY_EMPTY_ID,
      name: 'Familia Ortiz',
      is_active: false,
      student_count: 1,
    },
  ],
  total: 2,
  limit: 25,
  offset: 0,
};
export const EMPTY_FAMILIES_PAGE: FamilyListPage = {
  items: [],
  total: 0,
  limit: 25,
  offset: 0,
};

function charge(
  id: string,
  fields: Partial<ChargeItem> & Pick<ChargeItem, 'net' | 'open'>,
): ChargeItem {
  return {
    id,
    student: ANA,
    kind: 'session',
    service_date: '2026-10-01',
    due_date: '2026-10-01',
    description: null,
    list_amount: fields.net,
    discount_amount: '0.00',
    amount: fields.net,
    adjustments_total: '0.00',
    allocated: '0.00',
    status: 'pending',
    posted_at: '2026-10-01T16:00:00Z',
    posted_by_user_id: null,
    class_session_id: null,
    group_enrollment_id: null,
    cohort_enrollment_id: null,
    ...fields,
  };
}

/** Overdue, due 2026-09-15 (the oldest). */
export const CHARGE_OVERDUE = charge('53000000-0000-4000-8000-000000000001', {
  kind: 'tuition_period',
  description: 'Colegiatura septiembre',
  service_date: '2026-09-01',
  due_date: '2026-09-15',
  net: '1500.00',
  open: '1500.00',
  status: 'overdue',
});
/** Partially paid, due 2026-10-05. */
export const CHARGE_PARTIAL = charge('53000000-0000-4000-8000-000000000002', {
  student: LUIS,
  kind: 'package_purchase',
  service_date: '2026-10-05',
  due_date: '2026-10-05',
  net: '2000.00',
  allocated: '1200.00',
  open: '800.00',
  status: 'partially_paid',
});
/** Pending, due 2026-10-25. */
export const CHARGE_PENDING = charge('53000000-0000-4000-8000-000000000003', {
  service_date: '2026-10-18',
  due_date: '2026-10-25',
  net: '450.00',
  open: '450.00',
  status: 'pending',
});
export const CHARGE_PAID = charge('53000000-0000-4000-8000-000000000004', {
  kind: 'group_enrollment',
  description: 'Inscripción Inglés A1',
  service_date: '2026-08-20',
  due_date: '2026-08-20',
  net: '600.00',
  allocated: '600.00',
  open: '0.00',
  status: 'paid',
});

/** The open charges in the API's order (newest service date first). */
export const OPEN_CHARGES: ChargeItem[] = [
  CHARGE_PENDING,
  CHARGE_PARTIAL,
  CHARGE_OVERDUE,
];

export const CHARGES_PAGE: ChargePage = {
  items: [CHARGE_PENDING, CHARGE_PARTIAL, CHARGE_OVERDUE, CHARGE_PAID],
  total: 4,
  limit: 25,
  offset: 0,
};
export const EMPTY_CHARGES_PAGE: ChargePage = {
  items: [],
  total: 0,
  limit: 25,
  offset: 0,
};

export const SUMMARY: AccountSummary = {
  family: { id: FAMILY_ID, name: 'Familia López', is_active: true },
  account_id: '54000000-0000-4000-8000-000000000001',
  due_offset_days: 7,
  outstanding: '2750.00',
  account_credit: '300.00',
  net_balance: '2450.00',
  pending: '450.00',
  partially_paid: '800.00',
  overdue: '1500.00',
  students: [
    {
      student: ANA,
      outstanding: '1950.00',
      pending: '450.00',
      partially_paid: '0.00',
      overdue: '1500.00',
    },
    {
      student: LUIS,
      outstanding: '800.00',
      pending: '0.00',
      partially_paid: '800.00',
      overdue: '0.00',
    },
  ],
  today: TODAY,
};
export const SUMMARY_NO_CREDIT: AccountSummary = {
  ...SUMMARY,
  account_credit: '0.00',
  net_balance: '2750.00',
};
export const SUMMARY_NO_ACCOUNT: AccountSummary = {
  family: { id: FAMILY_ID, name: 'Familia López', is_active: true },
  account_id: null,
  due_offset_days: 0,
  outstanding: '0.00',
  account_credit: '0.00',
  net_balance: '0.00',
  pending: '0.00',
  partially_paid: '0.00',
  overdue: '0.00',
  students: [
    {
      student: ANA,
      outstanding: '0.00',
      pending: '0.00',
      partially_paid: '0.00',
      overdue: '0.00',
    },
  ],
  today: TODAY,
};

export const PAYMENT_ID = '55000000-0000-4000-8000-000000000001';
const PAYMENT_OLD_ID = '55000000-0000-4000-8000-000000000002';

export const LEDGER_ENTRIES: LedgerEntry[] = [
  {
    entry_type: 'allocation',
    entry_id: '56000000-0000-4000-8000-000000000001',
    kind: null,
    student_id: LUIS.id,
    charge_id: CHARGE_PARTIAL.id,
    payment_id: PAYMENT_ID,
    amount: '1200.00',
    balance_effect: '0.00',
    occurred_at: '2026-10-12T17:00:00Z',
  },
  {
    entry_type: 'payment',
    entry_id: PAYMENT_ID,
    kind: 'transfer',
    student_id: null,
    charge_id: null,
    payment_id: PAYMENT_ID,
    amount: '1500.00',
    balance_effect: '-1500.00',
    occurred_at: '2026-10-12T17:00:00Z',
  },
  {
    entry_type: 'charge',
    entry_id: CHARGE_PARTIAL.id,
    kind: 'package_purchase',
    student_id: LUIS.id,
    charge_id: CHARGE_PARTIAL.id,
    payment_id: null,
    amount: '2000.00',
    balance_effect: '2000.00',
    occurred_at: '2026-10-05T16:00:00Z',
  },
];
export const LEDGER_PAGE: LedgerPage = {
  items: LEDGER_ENTRIES,
  next_cursor: 'cursor-2',
};
export const LEDGER_PAGE_2: LedgerPage = {
  items: [
    {
      entry_type: 'adjustment',
      entry_id: '57000000-0000-4000-8000-000000000001',
      kind: 'cancellation_credit',
      student_id: ANA.id,
      charge_id: CHARGE_PAID.id,
      payment_id: null,
      amount: '-150.00',
      balance_effect: '-150.00',
      occurred_at: '2026-09-02T16:00:00Z',
    },
  ],
  next_cursor: null,
};
export const EMPTY_LEDGER_PAGE: LedgerPage = { items: [], next_cursor: null };

export const PAYMENT: PaymentItem = {
  id: PAYMENT_ID,
  amount: '1500.00',
  received_on: '2026-10-12',
  payment_method_code: 'transfer',
  reference: 'SPEI 99812',
  payer_name: 'María López',
  note: 'Pagó en ventanilla',
  recorded_by_user_id: '00000000-0000-4000-8000-000000000001',
  recorded_at: '2026-10-12T17:00:00Z',
  allocated_amount: '1200.00',
  refunded_amount: '0.00',
  unallocated_amount: '300.00',
  is_reversed: false,
};
export const PAYMENT_OLD: PaymentItem = {
  ...PAYMENT,
  id: PAYMENT_OLD_ID,
  amount: '600.00',
  received_on: '2026-08-20',
  payment_method_code: 'cash',
  reference: null,
  payer_name: null,
  note: null,
  recorded_at: '2026-08-20T17:00:00Z',
  allocated_amount: '600.00',
  unallocated_amount: '0.00',
};
export const PAYMENTS_PAGE: PaymentPage = {
  items: [PAYMENT, PAYMENT_OLD],
  total: 2,
  limit: 25,
  offset: 0,
};
export const EMPTY_PAYMENTS_PAGE: PaymentPage = {
  items: [],
  total: 0,
  limit: 25,
  offset: 0,
};

export const PAYMENT_DETAIL: PaymentDetail = {
  ...PAYMENT,
  allocations: [
    {
      id: '58000000-0000-4000-8000-000000000001',
      charge_id: CHARGE_PARTIAL.id,
      student_id: LUIS.id,
      amount: '1200.00',
      created_at: '2026-10-12T17:00:00Z',
      created_by_user_id: '00000000-0000-4000-8000-000000000001',
      is_valid: true,
    },
    {
      id: '58000000-0000-4000-8000-000000000002',
      charge_id: CHARGE_PAID.id,
      student_id: ANA.id,
      amount: '150.00',
      created_at: '2026-10-12T17:00:00Z',
      created_by_user_id: '00000000-0000-4000-8000-000000000001',
      is_valid: false,
    },
  ],
};

export const PAYMENT_METHODS: PaymentMethod[] = [
  { code: 'cash', is_active: true },
  { code: 'other', is_active: false },
  { code: 'transfer', is_active: true },
];

export const RECORDED: PaymentRecorded = {
  payment: {
    ...PAYMENT,
    id: '55000000-0000-4000-8000-000000000009',
    amount: '2000.00',
    received_on: TODAY,
    payment_method_code: 'cash',
    reference: null,
    payer_name: null,
    note: null,
    allocated_amount: '2000.00',
    unallocated_amount: '0.00',
  },
  allocations: [],
  unallocated_amount: '0.00',
  account_credit_after: '300.00',
};
