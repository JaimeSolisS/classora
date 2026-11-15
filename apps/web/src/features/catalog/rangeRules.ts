import type {
  AcademicPeriod,
  AcademicPeriodCreate,
  AcademicPeriodUpdate,
  Closure,
  ClosureCreate,
  ClosureType,
  ClosureUpdate,
  PeriodType,
} from './api';

// Pure helpers of the period and closure pages (#38 E2-E4): the request
// bodies from the form values, the span limits and the closures' year
// filter. An update sends the loaded `version` plus only the fields that
// changed; null means nothing changed (no request).

/** The API's span limits (models.PERIOD_MAX_SPAN_DAYS, CLOSURE_MAX_SPAN_DAYS). */
export const PERIOD_MAX_SPAN_DAYS = 730;
export const CLOSURE_MAX_SPAN_DAYS = 366;

export const PERIOD_TYPES: readonly PeriodType[] = [
  'school_year',
  'semester',
  'term',
];
export const CLOSURE_TYPES: readonly ClosureType[] = [
  'holiday',
  'closure',
  'vacation',
];

export interface RangeFormValues {
  /** Trimmed name. */
  name: string;
  type: string;
  /** `YYYY-MM-DD`. */
  startsOn: string;
  /** `YYYY-MM-DD`, inclusive. */
  endsOn: string;
}

/** Days from `startsOn` to `endsOn` (calendar dates, no zone). */
export function daysBetween(startsOn: string, endsOn: string): number {
  const day = (value: string) => {
    const [year, month, date] = value.split('-').map(Number) as [
      number,
      number,
      number,
    ];
    return Date.UTC(year, month - 1, date) / 86_400_000;
  };
  return day(endsOn) - day(startsOn);
}

export function periodCreateBody(
  values: RangeFormValues,
): AcademicPeriodCreate {
  return {
    name: values.name,
    period_type: values.type as PeriodType,
    starts_on: values.startsOn,
    ends_on: values.endsOn,
  };
}

export function periodUpdateBody(
  period: AcademicPeriod,
  values: RangeFormValues,
): AcademicPeriodUpdate | null {
  const body: AcademicPeriodUpdate = { version: period.version };
  if (values.name !== period.name) {
    body.name = values.name;
  }
  if (values.startsOn !== period.starts_on) {
    body.starts_on = values.startsOn;
  }
  if (values.endsOn !== period.ends_on) {
    body.ends_on = values.endsOn;
  }
  return Object.keys(body).length > 1 ? body : null;
}

export function closureCreateBody(values: RangeFormValues): ClosureCreate {
  return {
    name: values.name,
    closure_type: values.type as ClosureType,
    starts_on: values.startsOn,
    ends_on: values.endsOn,
  };
}

export function closureUpdateBody(
  closure: Closure,
  values: RangeFormValues,
): ClosureUpdate | null {
  const body: ClosureUpdate = { version: closure.version };
  if (values.name !== closure.name) {
    body.name = values.name;
  }
  if (values.type !== closure.closure_type) {
    body.closure_type = values.type as ClosureType;
  }
  if (values.startsOn !== closure.starts_on) {
    body.starts_on = values.startsOn;
  }
  if (values.endsOn !== closure.ends_on) {
    body.ends_on = values.endsOn;
  }
  return Object.keys(body).length > 1 ? body : null;
}

/** The closures page's years: the current year (center zone) ± 2. */
export function yearOptions(currentYear: number): number[] {
  return [-2, -1, 0, 1, 2].map((offset) => currentYear + offset);
}

/** The `from`/`to` query of a year: January 1 to December 31. */
export function yearRange(year: number): { from: string; to: string } {
  const padded = String(year).padStart(4, '0');
  return { from: `${padded}-01-01`, to: `${padded}-12-31` };
}

/** The years a link may open (`?year=`), beyond the select's five. */
export const YEAR_MIN = 1900;
export const YEAR_MAX = 2999;

/**
 * The `year` search parameter when it is a year from YEAR_MIN to YEAR_MAX,
 * else `fallback`. A linked year outside the select's options is added to
 * them by the page.
 */
export function yearParam(search: URLSearchParams, fallback: number): number {
  const raw = search.get('year');
  const year = raw && /^[0-9]{4}$/.test(raw) ? Number(raw) : NaN;
  return year >= YEAR_MIN && year <= YEAR_MAX ? year : fallback;
}
