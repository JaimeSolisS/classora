import type { ParseKeys } from 'i18next';
import type { ComponentType } from 'react';
import { canAccess, type AccessRequirement } from '@/features/auth/access';
import type { SessionUser } from '@/features/auth/session';
import {
  KPI_FINANCE_ACCESS,
  KPI_OPERATIONS_ACCESS,
  KPI_TUTOR_ACCESS,
} from '@/features/reporting/access';
import {
  BalancesKpiSection,
  CourseProfitabilitySection,
  RoomUtilizationSection,
  StudentRetentionSection,
  TutorPerformanceSection,
} from '@/features/reporting/kpis';
import type { LayoutEntry, WidgetCode } from './api';

// The dashboard widgets (M8-10 D2, #127 E3): the six M8-09b KPI sections,
// mounted unchanged, in the API registry's order (the role default). Each
// shows only with its KPI route's permission (the same requirement as on
// the KPI page); the API checks every request. K1 (revenue trend) needs
// the basis switch, so the page mounts it itself (`kind: 'trend'`).
// Imported only by the lazy dashboard page: Recharts stays out of the
// entry chunk.

export interface RangeWidgetProps {
  from: string;
  to: string;
}

export type Widget =
  | {
      code: WidgetCode;
      titleKey: ParseKeys<'common'>;
      access: AccessRequirement;
      kind: 'trend';
    }
  | {
      code: WidgetCode;
      titleKey: ParseKeys<'common'>;
      access: AccessRequirement;
      kind: 'range' | 'today';
      Component: ComponentType<RangeWidgetProps>;
    };

export const WIDGETS = [
  {
    code: 'revenue_trend',
    titleKey: 'reports.kpis.revenueTrend.title',
    access: KPI_FINANCE_ACCESS,
    kind: 'trend',
  },
  {
    code: 'balances',
    titleKey: 'reports.kpis.balances.title',
    access: KPI_FINANCE_ACCESS,
    kind: 'today',
    Component: BalancesKpiSection,
  },
  {
    code: 'course_profitability',
    titleKey: 'reports.kpis.courseProfitability.title',
    access: KPI_FINANCE_ACCESS,
    kind: 'range',
    Component: CourseProfitabilitySection,
  },
  {
    code: 'room_utilization',
    titleKey: 'reports.kpis.rooms.title',
    access: KPI_OPERATIONS_ACCESS,
    kind: 'range',
    Component: RoomUtilizationSection,
  },
  {
    code: 'student_retention',
    titleKey: 'reports.kpis.retention.title',
    access: KPI_OPERATIONS_ACCESS,
    kind: 'range',
    Component: StudentRetentionSection,
  },
  {
    code: 'tutor_performance',
    titleKey: 'reports.kpis.tutors.title',
    access: KPI_TUTOR_ACCESS,
    kind: 'range',
    Component: TutorPerformanceSection,
  },
] as const satisfies readonly Widget[];

// D2: `tsc` fails when these codes and the API's WidgetCode union differ.
type Same<A, B> = [A] extends [B] ? ([B] extends [A] ? true : false) : false;
type Assert<T extends true> = T;
export type WidgetCodesMatchTheApi = Assert<
  Same<(typeof WIDGETS)[number]['code'], WidgetCode>
>;

const BY_CODE: ReadonlyMap<string, Widget> = new Map(
  WIDGETS.map((widget) => [widget.code, widget]),
);

/** The widget of `code`, or undefined for a code this build doesn't know. */
export function widgetOf(code: string): Widget | undefined {
  return BY_CODE.get(code);
}

/** Whether `user` passes the widget's KPI requirement (F3, D4). */
export function mayShow(
  user: SessionUser | null,
  code: string,
): code is WidgetCode {
  const widget = widgetOf(code);
  return widget !== undefined && canAccess(user, widget.access);
}

/** The role default (D5): every widget `user` may see, shown, in order. */
export function defaultLayout(user: SessionUser | null): LayoutEntry[] {
  return WIDGETS.filter((widget) => canAccess(user, widget.access)).map(
    (widget) => ({ widget: widget.code, visible: true }),
  );
}
