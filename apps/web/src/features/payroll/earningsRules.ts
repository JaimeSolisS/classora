import type { PayslipLine } from './api';
import { isNegativeAmount, isSessionLine } from './periodRules';

// The tutor's earnings pages' client rules (M7-10b), without React: the
// detail page's path, the list's page in the URL, the payslip's groups and
// the class time in hours and minutes. Nothing is added up here: every amount
// is the API's string (#113 C3).

/** A finalized period's page under My pay (E1). */
export function myPayPeriodPath(periodId: string): string {
  return `/my-pay/periods/${encodeURIComponent(periodId)}`;
}

/** The list's 1-based page from `?page=`; anything invalid is page 1. */
export function parseEarningsPage(params: URLSearchParams): number {
  const page = Number.parseInt(params.get('page') ?? '', 10);
  return Number.isInteger(page) && page >= 1 ? page : 1;
}

export const EARNINGS_GROUPS = [
  'classes',
  'salary',
  'bonuses',
  'deductions',
  'adjustments',
] as const;

export type EarningsGroup = (typeof EARNINGS_GROUPS)[number];

/**
 * The lines in the payslip's groups (C3), each in the API's order: the
 * session kinds are classes, a pay item above zero a bonus and any other a
 * deduction (the payslip's own rule), every other kind an adjustment.
 */
export function groupEarningsLines(
  lines: readonly PayslipLine[],
): Record<EarningsGroup, PayslipLine[]> {
  const groups: Record<EarningsGroup, PayslipLine[]> = {
    classes: [],
    salary: [],
    bonuses: [],
    deductions: [],
    adjustments: [],
  };
  for (const line of lines) {
    if (isSessionLine(line)) {
      groups.classes.push(line);
    } else if (line.kind === 'salary') {
      groups.salary.push(line);
    } else if (line.kind === 'pay_item') {
      const positive = !isNegativeAmount(line.amount) && !isZero(line.amount);
      groups[positive ? 'bonuses' : 'deductions'].push(line);
    } else {
      groups.adjustments.push(line);
    }
  }
  return groups;
}

/** Whether an API decimal string is zero ("0.00", "-0.00"). */
export function isZero(amount: string): boolean {
  return /^-?0+(\.0+)?$/.test(amount);
}

/** Class minutes as whole hours and the minutes left (C2). */
export function hoursAndMinutes(total: number): {
  hours: number;
  minutes: number;
} {
  const safe = Number.isInteger(total) && total > 0 ? total : 0;
  return { hours: Math.floor(safe / 60), minutes: safe % 60 };
}
