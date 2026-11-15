import { parseLocalDate, type LocalDate } from '@/lib/time';
import type { GroupBy, HistoryQuery } from './api';

// The academic history's controls in the URL (#308 E1, E8; the calendar's
// urlState style): `group` (school_year by default, semester, term, none),
// `view` (grouped by default, or list), `subject` (a UUID), `from` and `to`
// (local dates), the staff page's `tab` (history by default, or
// evaluations) and the family page's `student`. Anything invalid is
// ignored. Pure.

export type HistoryView = 'grouped' | 'list';
export type HistoryTab = 'history' | 'evaluations';

export interface HistoryControls {
  group: GroupBy;
  view: HistoryView;
  subject: string | null;
  from: LocalDate | null;
  to: LocalDate | null;
}

export const GROUPS: readonly GroupBy[] = [
  'school_year',
  'semester',
  'term',
  'none',
];

const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;

export function uuidParam(value: string | null): string | null {
  return value !== null && UUID.test(value) ? value.toLowerCase() : null;
}

function dateParam(value: string | null): LocalDate | null {
  if (value === null) {
    return null;
  }
  try {
    return parseLocalDate(value);
  } catch {
    return null;
  }
}

export function parseHistoryControls(params: URLSearchParams): HistoryControls {
  const group = params.get('group');
  return {
    group: GROUPS.find((value) => value === group) ?? 'school_year',
    view: params.get('view') === 'list' ? 'list' : 'grouped',
    subject: uuidParam(params.get('subject')),
    from: dateParam(params.get('from')),
    to: dateParam(params.get('to')),
  };
}

/**
 * The params with these controls set (only what differs from the default);
 * other params (`tab`, `student`) are kept.
 */
export function withHistoryControls(
  params: URLSearchParams,
  controls: HistoryControls,
): URLSearchParams {
  const next = new URLSearchParams(params);
  for (const name of ['group', 'view', 'subject', 'from', 'to']) {
    next.delete(name);
  }
  if (controls.group !== 'school_year') next.set('group', controls.group);
  if (controls.view !== 'grouped') next.set('view', controls.view);
  if (controls.subject) next.set('subject', controls.subject);
  if (controls.from) next.set('from', controls.from);
  if (controls.to) next.set('to', controls.to);
  return next;
}

/** A filter is set (the subject or a date; grouping and view aren't). */
export function hasHistoryFilters(controls: HistoryControls): boolean {
  return (
    controls.subject !== null || controls.from !== null || controls.to !== null
  );
}

/** `to` before `from`: the range error, and no request is sent (E1). */
export function historyRangeInvalid(controls: HistoryControls): boolean {
  return (
    controls.from !== null &&
    controls.to !== null &&
    controls.to < controls.from
  );
}

/** The history read's query of the controls. */
export function historyQuery(controls: HistoryControls): HistoryQuery {
  return {
    group: controls.group,
    subject: controls.subject,
    from: controls.from,
    to: controls.to,
  };
}

export function parseTab(params: URLSearchParams): HistoryTab {
  return params.get('tab') === 'evaluations' ? 'evaluations' : 'history';
}
