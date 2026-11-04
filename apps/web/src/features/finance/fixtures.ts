import type { Session } from '@/features/auth/session';
import {
  VENDORS_PAGE_SIZE,
  type ExpenseCategory,
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
