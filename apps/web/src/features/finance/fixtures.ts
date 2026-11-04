import type { Session } from '@/features/auth/session';
import type { LocalDate } from '@/lib/time';
import {
  EXPENSES_PAGE_SIZE,
  VENDORS_PAGE_SIZE,
  type ExpenseCategory,
  type ExpenseDetail,
  type ExpenseItem,
  type ExpensePage,
  type ExpensePayment,
  type ExpenseTemplate,
  type Vendor,
  type VendorPage,
} from './api';

// Expense categories and vendors for the Vitest suites and the stories
// (M7-11b F1, F2). The seeded rows carry their Spanish name and their key,
// as migration 0083 stores them; names and contacts are synthetic.

const category = (
  n: number,
  fields: Partial<ExpenseCategory> & Pick<ExpenseCategory, 'name'>,
): ExpenseCategory => ({
  id: `83000000-0000-4000-8000-${String(n).padStart(12, '0')}`,
  system_key: null,
  is_active: true,
  version: 1,
  ...fields,
});

export const CAT_RENT = category(1, { name: 'Renta', system_key: 'rent' });
export const CAT_UTILITIES = category(2, {
  name: 'Servicios',
  system_key: 'utilities',
});
export const CAT_SUPPLIES = category(3, {
  name: 'Insumos',
  system_key: 'supplies',
});
export const CAT_EQUIPMENT = category(4, {
  name: 'Equipo',
  system_key: 'equipment',
});
export const CAT_MAINTENANCE = category(5, {
  name: 'Mantenimiento',
  system_key: 'maintenance',
});

/** The five seeded categories, in the API's order (active, by name). */
export const SEEDED_CATEGORIES: ExpenseCategory[] = [
  CAT_EQUIPMENT,
  CAT_SUPPLIES,
  CAT_MAINTENANCE,
  CAT_RENT,
  CAT_UTILITIES,
];

/** A seeded category renamed: its key is gone, its text shows everywhere. */
export const CAT_RENAMED = category(2, {
  name: 'Luz y agua',
  version: 2,
});
/** A custom category, and a deactivated one. */
export const CAT_CUSTOM = category(6, { name: 'Publicidad' });
export const CAT_RETIRED = category(7, {
  name: 'Cafetería',
  is_active: false,
  version: 3,
});

/** Seeded, renamed, custom and inactive rows, in the API's order. */
export const MIXED_CATEGORIES: ExpenseCategory[] = [
  CAT_EQUIPMENT,
  CAT_SUPPLIES,
  CAT_RENAMED,
  CAT_MAINTENANCE,
  CAT_CUSTOM,
  CAT_RENT,
  CAT_RETIRED,
];

const vendor = (
  n: number,
  fields: Partial<Vendor> & Pick<Vendor, 'name'>,
): Vendor => ({
  id: `83100000-0000-4000-8000-${String(n).padStart(12, '0')}`,
  contact: null,
  is_active: true,
  version: 1,
  ...fields,
});

export const VENDOR_PAPER = vendor(1, {
  name: 'Papelería Ejemplo',
  contact: 'ventas@papeleria.example\n55 0000 0001',
});
export const VENDOR_CLEANING = vendor(2, { name: 'Limpieza Muestra' });
export const VENDOR_LONG = vendor(3, {
  name: 'Servicios Integrales de Mantenimiento y Reparación Ficticios del Centro',
  contact:
    'Contacto: Persona Ficticia, extensión 1234, horario de 9 a 18 h, correo-muy-largo-sin-espacios@proveedor-ficticio.example',
});
export const VENDOR_RETIRED = vendor(4, {
  name: 'Agua Purificada Demo',
  contact: '55 0000 0004',
  is_active: false,
  version: 2,
});

export const VENDORS: Vendor[] = [
  VENDOR_CLEANING,
  VENDOR_PAPER,
  VENDOR_LONG,
  VENDOR_RETIRED,
];

export function vendorPage(
  items: Vendor[] = VENDORS,
  {
    total = items.length,
    offset = 0,
  }: { total?: number; offset?: number } = {},
): VendorPage {
  return { items, total, limit: VENDORS_PAGE_SIZE, offset };
}

/** Storybook: a signed-in Administrator, who sees the Expenses tabs. */
export const EXPENSES_SESSION: Session = {
  status: 'signedIn',
  user: {
    id: '00000000-0000-4000-8000-000000000001',
    username: 'demo.admin',
    display_name: 'Demo Admin',
    privacy_notice_required: false,
    preferences: { language: null, theme: null },
    center_time_zone: 'America/Mexico_City',
    roles: ['administrator'],
    permissions: ['user.view', 'expense.configure'],
  },
};

// ------------------------------------------------- expenses (M7-12b F1, F2)

/** The center's today in these fixtures. */
export const EXPENSE_TODAY = '2026-10-20' as LocalDate;

const ref = (category: ExpenseCategory) => ({
  id: category.id,
  name: category.name,
  system_key: category.system_key,
  is_active: category.is_active,
});
const vendorRef = (row: Vendor) => ({
  id: row.id,
  name: row.name,
  is_active: row.is_active,
});
const PERSON = {
  id: '00000000-0000-4000-8000-000000000001',
  display_name: 'Demo Owner',
};
const FINANCE_PERSON = {
  id: '00000000-0000-4000-8000-000000000003',
  display_name: 'Demo Finance',
};

const expenseId = (n: number) =>
  `84000000-0000-4000-8000-${String(n).padStart(12, '0')}`;
const paymentId = (n: number) =>
  `84100000-0000-4000-8000-${String(n).padStart(12, '0')}`;

export function expenseDetail(
  n: number,
  fields: Partial<ExpenseDetail> = {},
): ExpenseDetail {
  return {
    id: expenseId(n),
    category: ref(CAT_RENT),
    vendor: vendorRef(VENDOR_PAPER),
    description: `Gasto de prueba ${n}`,
    amount: '1000.00',
    adjusted_amount: '1000.00',
    paid_amount: '0.00',
    open_amount: '1000.00',
    status: 'unpaid',
    incurred_on: '2026-10-10',
    due_on: '2026-10-30',
    receipt_reference: null,
    template: null,
    created_at: '2026-10-10T16:00:00Z',
    note: null,
    created_by: PERSON,
    payments: [],
    adjustments: [],
    ...fields,
  };
}

export function payment(
  n: number,
  fields: Partial<ExpensePayment> = {},
): ExpensePayment {
  return {
    id: paymentId(n),
    paid_on: '2026-10-15',
    amount: '400.00',
    method_code: 'transfer',
    reference: `SPEI ${n}`,
    note: null,
    recorded_by: FINANCE_PERSON,
    created_at: '2026-10-15T17:00:00Z',
    reversal: null,
    ...fields,
  };
}

export const PAYMENT_VALID = payment(1);
export const PAYMENT_REVERSED = payment(2, {
  amount: '300.00',
  reversal: {
    reason: 'Monto equivocado',
    reversed_by: PERSON,
    created_at: '2026-10-16T15:00:00Z',
  },
});

export const EXPENSE_UNPAID = expenseDetail(1, {
  receipt_reference: 'F-1001',
  note: 'Renta de octubre\nPagar por transferencia',
});
export const EXPENSE_PARTIAL = expenseDetail(2, {
  category: ref(CAT_UTILITIES),
  vendor: null,
  description: 'Luz de octubre',
  paid_amount: '400.00',
  open_amount: '600.00',
  status: 'partially_paid',
  payments: [PAYMENT_VALID, PAYMENT_REVERSED],
});
export const EXPENSE_OVERDUE = expenseDetail(3, {
  category: ref(CAT_RETIRED),
  vendor: vendorRef(VENDOR_RETIRED),
  description: 'Café para la sala',
  amount: '250.00',
  adjusted_amount: '250.00',
  open_amount: '250.00',
  incurred_on: '2026-09-01',
  due_on: '2026-09-15',
  status: 'overdue',
});
export const EXPENSE_PAID = expenseDetail(4, {
  description: 'Papelería',
  amount: '400.00',
  adjusted_amount: '400.00',
  paid_amount: '400.00',
  open_amount: '0.00',
  status: 'paid',
  payments: [PAYMENT_VALID],
});
export const EXPENSE_VOID = expenseDetail(5, {
  description: 'Gasto capturado por error',
  adjusted_amount: '0.00',
  open_amount: '0.00',
  status: 'void',
  adjustments: [
    {
      id: '84200000-0000-4000-8000-000000000001',
      amount: '-1000.00',
      reason: 'Categoría equivocada',
      created_by: PERSON,
      created_at: '2026-10-11T15:00:00Z',
    },
  ],
});

/** A list item from a detail (the item has no note, payments or adjustments). */
export function expenseItem(detail: ExpenseDetail): ExpenseItem {
  return {
    id: detail.id,
    category: detail.category,
    vendor: detail.vendor,
    description: detail.description,
    amount: detail.amount,
    adjusted_amount: detail.adjusted_amount,
    paid_amount: detail.paid_amount,
    open_amount: detail.open_amount,
    status: detail.status,
    incurred_on: detail.incurred_on,
    due_on: detail.due_on,
    receipt_reference: detail.receipt_reference,
    template: detail.template,
    created_at: detail.created_at,
  };
}

export const EXPENSE_ITEMS: ExpenseItem[] = [
  EXPENSE_UNPAID,
  EXPENSE_PARTIAL,
  EXPENSE_OVERDUE,
  EXPENSE_PAID,
  EXPENSE_VOID,
].map(expenseItem);

export function expensePage(
  items: ExpenseItem[] = EXPENSE_ITEMS,
  {
    total = items.length,
    offset = 0,
  }: { total?: number; offset?: number } = {},
): ExpensePage {
  return { items, total, limit: EXPENSES_PAGE_SIZE, offset };
}

export const EXPENSE_METHODS = [
  { code: 'cash', is_active: true },
  { code: 'other', is_active: false },
  { code: 'transfer', is_active: true },
];

/** Storybook: the signed-in Owner (every expense action). */
export const EXPENSES_OWNER_SESSION: Session = {
  status: 'signedIn',
  user: {
    id: '00000000-0000-4000-8000-000000000001',
    username: 'demo.owner',
    privacy_notice_required: false,
    preferences: { language: null, theme: null },
    center_time_zone: 'America/Mexico_City',
    display_name: 'Demo Owner',
    roles: ['owner'],
    permissions: [
      'expense.configure',
      'expense.view',
      'expense.manage',
      'finance.refund.approve',
    ],
  },
};

// --------------------------------- recurring templates (M7-13b H1, H2)

const templateId = (n: number) =>
  `85000000-0000-4000-8000-${String(n).padStart(12, '0')}`;

export function expenseTemplate(
  n: number,
  fields: Partial<ExpenseTemplate> = {},
): ExpenseTemplate {
  return {
    id: templateId(n),
    name: `Plantilla ${n}`,
    category: ref(CAT_RENT),
    vendor: vendorRef(VENDOR_PAPER),
    amount: '12500.00',
    frequency: 'monthly',
    starts_on: '2026-09-01',
    ends_on: null,
    due_offset_days: 5,
    next_period_start: '2026-11-01',
    last_period_start: '2026-10-01',
    status: 'active',
    is_active: true,
    version: 1,
    created_at: '2026-09-01T15:00:00Z',
    updated_at: '2026-09-01T15:00:00Z',
    ...fields,
  };
}

/** Monthly rent, two periods generated. */
export const TEMPLATE_RENT = expenseTemplate(1, { name: 'Renta del local' });
/** Weekly cleaning, paused, with no vendor. */
export const TEMPLATE_PAUSED = expenseTemplate(2, {
  name: 'Limpieza semanal',
  category: ref(CAT_MAINTENANCE),
  vendor: null,
  amount: '800.00',
  frequency: 'weekly',
  starts_on: '2026-10-05',
  next_period_start: '2026-10-26',
  last_period_start: '2026-10-19',
  due_offset_days: 0,
  status: 'paused',
  is_active: false,
  version: 2,
});
/** Annual, ended, on an inactive category and vendor. */
export const TEMPLATE_ENDED = expenseTemplate(3, {
  name: 'Garrafones de agua',
  category: ref(CAT_RETIRED),
  vendor: vendorRef(VENDOR_RETIRED),
  amount: '3000.00',
  frequency: 'annual',
  starts_on: '2025-10-01',
  ends_on: '2026-09-30',
  next_period_start: '2027-10-01',
  last_period_start: '2025-10-01',
  due_offset_days: 1,
  status: 'ended',
  version: 3,
});
/** Starts next month: nothing generated yet. */
export const TEMPLATE_FUTURE = expenseTemplate(4, {
  name: 'Internet',
  category: ref(CAT_UTILITIES),
  vendor: vendorRef(VENDOR_CLEANING),
  amount: '650.00',
  starts_on: '2026-11-15',
  next_period_start: '2026-11-15',
  last_period_start: null,
  due_offset_days: 10,
});

export const TEMPLATES: ExpenseTemplate[] = [
  TEMPLATE_RENT,
  TEMPLATE_FUTURE,
  TEMPLATE_PAUSED,
  TEMPLATE_ENDED,
];

/** The expenses TEMPLATE_RENT generated, newest period first. */
export const GENERATED_EXPENSES: ExpenseItem[] = [
  expenseDetail(11, {
    description: TEMPLATE_RENT.name,
    amount: '12500.00',
    adjusted_amount: '12500.00',
    open_amount: '12500.00',
    incurred_on: '2026-10-01',
    due_on: '2026-10-06',
    status: 'overdue',
    template: { id: TEMPLATE_RENT.id, name: TEMPLATE_RENT.name },
    created_by: null,
  }),
  expenseDetail(12, {
    description: TEMPLATE_RENT.name,
    amount: '12500.00',
    adjusted_amount: '12500.00',
    paid_amount: '12500.00',
    open_amount: '0.00',
    incurred_on: '2026-09-01',
    due_on: '2026-09-06',
    status: 'paid',
    template: { id: TEMPLATE_RENT.id, name: TEMPLATE_RENT.name },
    created_by: null,
  }),
].map(expenseItem);

/** A generated expense's detail (M7-13b E1). */
export const EXPENSE_GENERATED = expenseDetail(11, {
  description: TEMPLATE_RENT.name,
  amount: '12500.00',
  adjusted_amount: '12500.00',
  open_amount: '12500.00',
  incurred_on: '2026-10-01',
  due_on: '2026-10-06',
  status: 'overdue',
  template: { id: TEMPLATE_RENT.id, name: TEMPLATE_RENT.name },
  created_by: null,
});
