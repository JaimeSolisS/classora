import { z } from '@/lib/forms';
import type {
  BusinessSettings,
  BusinessSettingsUpdate,
  OperatingHoursItem,
} from './api';

// Pure helpers of the settings page (#37 H3, H4): the form's Zod schema
// (mirroring BusinessSettingsUpdate), the form values of the stored
// settings and the PATCH body. UX only: the API validates every request.

/** The API's limits (apps/api/app/modules/administration). */
export const CENTER_NAME_MAX_LENGTH = 100;
export const EMAIL_MAX_LENGTH = 254;
export const PHONE_MAX_LENGTH = 32;
export const ADDRESS_MAX_LENGTH = 300;

// The #31 contact patterns, as the API checks them.
const EMAIL_PATTERN = /^[^@\s]+@[^@\s]+\.[^@\s]+$/;
const PHONE_PATTERN = /^\+?[0-9][0-9 ().-]{5,30}[0-9]$/;
const LOCAL_TIME = /^([01][0-9]|2[0-3]):[0-5][0-9]$/;

/** The time inputs' step: 15 minutes, in seconds. */
export const TIME_STEP_SECONDS = 900;

/** Mexico's IANA zones, offered in the select (the API accepts any zone). */
export const MEXICO_TIME_ZONES = [
  'America/Mexico_City',
  'America/Cancun',
  'America/Merida',
  'America/Monterrey',
  'America/Matamoros',
  'America/Chihuahua',
  'America/Ciudad_Juarez',
  'America/Ojinaga',
  'America/Mazatlan',
  'America/Bahia_Banderas',
  'America/Hermosillo',
  'America/Tijuana',
] as const;

/** The ISO weekdays, Monday (1) to Sunday (7), in display order. */
export const WEEKDAYS = [1, 2, 3, 4, 5, 6, 7] as const;
export type Weekday = (typeof WEEKDAYS)[number];

/** The catalog key of each weekday's name. */
export const WEEKDAY_KEYS = {
  1: 'settings.weekdays.monday',
  2: 'settings.weekdays.tuesday',
  3: 'settings.weekdays.wednesday',
  4: 'settings.weekdays.thursday',
  5: 'settings.weekdays.friday',
  6: 'settings.weekdays.saturday',
  7: 'settings.weekdays.sunday',
} as const satisfies Record<Weekday, string>;

export type SettingsMessageKey =
  | 'settings.validation.nameRequired'
  | 'settings.validation.nameTooLong'
  | 'settings.validation.emailInvalid'
  | 'settings.validation.phoneInvalid'
  | 'settings.validation.addressTooLong'
  | 'settings.validation.timeRequired'
  | 'settings.validation.closesAfterOpens';

export interface DayValues {
  open: boolean;
  /** `HH:mm`, or empty while the day is closed. */
  opensAt: string;
  closesAt: string;
}

export interface SettingsFormValues {
  centerName: string;
  phone: string;
  email: string;
  address: string;
  timeZone: string;
  /** Monday to Sunday (index 0 is weekday 1). */
  days: DayValues[];
}

const optionalPattern = (
  pattern: RegExp,
  max: number,
  message: SettingsMessageKey,
) =>
  z
    .string()
    .trim()
    .refine(
      (value) => value === '' || (value.length <= max && pattern.test(value)),
      message,
    );

const daySchema = z
  .object({ open: z.boolean(), opensAt: z.string(), closesAt: z.string() })
  .superRefine((day, context) => {
    if (!day.open) {
      return;
    }
    if (!LOCAL_TIME.test(day.opensAt)) {
      context.addIssue({
        code: 'custom',
        path: ['opensAt'],
        message: 'settings.validation.timeRequired',
      });
    }
    if (!LOCAL_TIME.test(day.closesAt)) {
      context.addIssue({
        code: 'custom',
        path: ['closesAt'],
        message: 'settings.validation.timeRequired',
      });
    } else if (LOCAL_TIME.test(day.opensAt) && day.closesAt <= day.opensAt) {
      // HH:mm strings compare like the times they hold.
      context.addIssue({
        code: 'custom',
        path: ['closesAt'],
        message: 'settings.validation.closesAfterOpens',
      });
    }
  });

export const settingsFormSchema = z.object({
  centerName: z
    .string()
    .trim()
    .min(1, 'settings.validation.nameRequired')
    .max(CENTER_NAME_MAX_LENGTH, 'settings.validation.nameTooLong'),
  phone: optionalPattern(
    PHONE_PATTERN,
    PHONE_MAX_LENGTH,
    'settings.validation.phoneInvalid',
  ),
  email: optionalPattern(
    EMAIL_PATTERN,
    EMAIL_MAX_LENGTH,
    'settings.validation.emailInvalid',
  ),
  address: z
    .string()
    .trim()
    .max(ADDRESS_MAX_LENGTH, 'settings.validation.addressTooLong'),
  timeZone: z.string().min(1),
  days: z.array(daySchema).length(WEEKDAYS.length),
});

/** The form's values for the stored settings. */
export function valuesOf(settings: BusinessSettings): SettingsFormValues {
  const byWeekday = new Map(
    settings.operating_hours.map((item) => [item.weekday, item]),
  );
  return {
    centerName: settings.center_name,
    phone: settings.contact_phone ?? '',
    email: settings.contact_email ?? '',
    address: settings.address ?? '',
    timeZone: settings.time_zone,
    days: WEEKDAYS.map((weekday) => {
      const item = byWeekday.get(weekday);
      return item
        ? { open: true, opensAt: item.opens_at, closesAt: item.closes_at }
        : { open: false, opensAt: '', closesAt: '' };
    }),
  };
}

/** The open days as API entries, by weekday. */
export function hoursOf(days: readonly DayValues[]): OperatingHoursItem[] {
  return days.flatMap((day, index) =>
    day.open
      ? [{ weekday: index + 1, opens_at: day.opensAt, closes_at: day.closesAt }]
      : [],
  );
}

function sameHours(
  a: readonly OperatingHoursItem[],
  b: readonly OperatingHoursItem[],
): boolean {
  const key = (items: readonly OperatingHoursItem[]) =>
    JSON.stringify(
      [...items]
        .sort((x, y) => x.weekday - y.weekday)
        .map((i) => [i.weekday, i.opens_at, i.closes_at]),
    );
  return key(a) === key(b);
}

/**
 * The PATCH body: `version`, plus each field that changed; the hours, when
 * any day changed, as the whole week. An emptied contact field is `null`.
 */
export function updateBody(
  settings: BusinessSettings,
  values: SettingsFormValues,
): BusinessSettingsUpdate {
  const body: BusinessSettingsUpdate = { version: settings.version };
  const name = values.centerName.trim();
  if (name !== settings.center_name) {
    body.center_name = name;
  }
  const optional = (value: string) => value.trim() || null;
  if (optional(values.phone) !== settings.contact_phone) {
    body.contact_phone = optional(values.phone);
  }
  if (optional(values.email) !== settings.contact_email) {
    body.contact_email = optional(values.email);
  }
  if (optional(values.address) !== settings.address) {
    body.address = optional(values.address);
  }
  if (values.timeZone !== settings.time_zone) {
    body.time_zone = values.timeZone;
  }
  const hours = hoursOf(values.days);
  if (!sameHours(hours, settings.operating_hours)) {
    body.operating_hours = hours;
  }
  return body;
}

/** The zones the select offers: Mexico's, plus the stored one if another. */
export function zoneOptions(stored: string): string[] {
  return (MEXICO_TIME_ZONES as readonly string[]).includes(stored)
    ? [...MEXICO_TIME_ZONES]
    : [stored, ...MEXICO_TIME_ZONES];
}
