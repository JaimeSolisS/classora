import type { Meta, StoryObj } from '@storybook/react-vite';
import type { ReactNode } from 'react';
import { expect, fn } from 'storybook/test';
import { ApiError } from '@/app/api';
import { i18n } from '@/i18n';
import { StoryAuth } from '@/test/StoryAuth';
import { StoryQueryClient } from '@/test/StoryQueryClient';
import { StoryRouter } from '@/test/StoryRouter';
import { BALANCES_KEY } from '../api';
import { KpiPage } from '../KpiPage';
import { kpiKeys } from './api';
import { BalancesKpiView } from './BalancesKpiSection';
import { CourseProfitabilityView } from './CourseProfitabilitySection';
import {
  COURSES,
  COURSES_EMPTY,
  KPI_BALANCES,
  KPI_BALANCES_EMPTY,
  KPI_OWNER_SESSION,
  RETENTION,
  RETENTION_EMPTY,
  ROOMS,
  ROOMS_EDGES,
  ROOMS_EMPTY,
  TREND_ACCRUAL,
  TREND_CASH,
  TREND_EMPTY,
  TUTORS,
  TUTORS_EMPTY,
} from './fixtures';
import { RevenueTrendView } from './RevenueTrendSection';
import { RoomUtilizationView } from './RoomUtilizationSection';
import { StudentRetentionView } from './StudentRetentionSection';
import { TutorPerformanceView } from './TutorPerformanceSection';

// The KPI page (M8-09b E2): each section with data, all-zero ("No activity
// in this period") and a failed read, from #126's worked examples B1–B5,
// then the whole page for the Owner (seeded queries, no request) and at
// 320 px. axe runs on each story in both themes.

const narrow = {
  parameters: {
    viewport: {
      options: {
        narrow320: {
          name: 'Narrow (320 px)',
          styles: { width: '320px', height: '640px' },
          type: 'mobile',
        },
      },
    },
  },
  globals: { viewport: { value: 'narrow320' } },
};

const RANGE = { from: '2031-03-01', to: '2031-03-31' };

function Shell({ children }: { children: ReactNode }) {
  return (
    <StoryAuth session={KPI_OWNER_SESSION}>
      <StoryRouter
        path="/reports/kpis"
        routes={[{ path: '*', element: <main>{children}</main> }]}
      />
    </StoryAuth>
  );
}

const failed = {
  status: 'error' as const,
  error: new ApiError({ status: 400, code: 'database_unavailable' }),
  onRetry: fn(),
};
const ready = <T,>(data: T) => ({ status: 'ready' as const, data });
const links = { overdueAccounts: true, overdueExpenses: true };

const meta = {
  title: 'Reports/KPIs',
  render: () => <></>,
} satisfies Meta;

export default meta;
type Story = StoryObj<typeof meta>;

const emptyText = () => i18n.t('reports.kpis.empty');

/** K1 (#126 B1) on the accrual basis: partial months, a negative month. */
export const RevenueTrend: Story = {
  render: () => (
    <Shell>
      <RevenueTrendView
        state={ready(TREND_ACCRUAL)}
        basis="accrual"
        onBasis={fn()}
      />
    </Shell>
  ),
  play: async ({ canvas }) => {
    await expect(canvas.getAllByText('-$850.00')[0]).toBeVisible();
    await expect(
      canvas.getAllByText(i18n.t('reports.kpis.revenueTrend.totals'))[0],
    ).toBeVisible();
  },
};

/** K1 (#126 B1) on the cash basis. */
export const RevenueTrendCash: Story = {
  render: () => (
    <Shell>
      <RevenueTrendView state={ready(TREND_CASH)} basis="cash" onBasis={fn()} />
    </Shell>
  ),
};

export const RevenueTrendEmpty: Story = {
  render: () => (
    <Shell>
      <RevenueTrendView
        state={ready(TREND_EMPTY)}
        basis="accrual"
        onBasis={fn()}
      />
    </Shell>
  ),
  play: async ({ canvas }) => {
    await expect(canvas.getByText(emptyText())).toBeVisible();
  },
};

export const RevenueTrendError: Story = {
  render: () => (
    <Shell>
      <RevenueTrendView state={failed} basis="accrual" onBasis={fn()} />
    </Shell>
  ),
};

/** K3: unpaid payroll first, then today's balances. */
export const Balances: Story = {
  render: () => (
    <Shell>
      <BalancesKpiView state={ready(KPI_BALANCES)} links={links} />
    </Shell>
  ),
  play: async ({ canvas }) => {
    await expect(
      canvas.getAllByText(i18n.t('reports.kpis.balances.payrollUnpaid'))[0],
    ).toBeVisible();
  },
};

export const BalancesEmpty: Story = {
  render: () => (
    <Shell>
      <BalancesKpiView state={ready(KPI_BALANCES_EMPTY)} links={links} />
    </Shell>
  ),
};

export const BalancesError: Story = {
  render: () => (
    <Shell>
      <BalancesKpiView state={failed} links={links} />
    </Shell>
  ),
};

/** K2 (#126 B2): C2, C1, "No cohort assigned" and the total. */
export const CourseProfitability: Story = {
  render: () => (
    <Shell>
      <CourseProfitabilityView state={ready(COURSES)} />
    </Shell>
  ),
  play: async ({ canvas }) => {
    await expect(canvas.getAllByText('$1,716.67')[0]).toBeVisible();
  },
};

export const CourseProfitabilityEmpty: Story = {
  render: () => (
    <Shell>
      <CourseProfitabilityView state={ready(COURSES_EMPTY)} />
    </Shell>
  ),
};

export const CourseProfitabilityError: Story = {
  render: () => (
    <Shell>
      <CourseProfitabilityView state={failed} />
    </Shell>
  ),
};

/** K4 (#126 B3): rooms A and B. */
export const RoomUtilization: Story = {
  render: () => (
    <Shell>
      <RoomUtilizationView state={ready(ROOMS)} />
    </Shell>
  ),
};

/** K4: no schedule available ("—") and a booking over the room's hours. */
export const RoomUtilizationEdges: Story = {
  render: () => (
    <Shell>
      <RoomUtilizationView state={ready(ROOMS_EDGES)} />
    </Shell>
  ),
  play: async ({ canvas }) => {
    await expect(
      canvas.getByText(i18n.t('reports.kpis.rooms.over100')),
    ).toBeVisible();
  },
};

export const RoomUtilizationEmpty: Story = {
  render: () => (
    <Shell>
      <RoomUtilizationView state={ready(ROOMS_EMPTY)} />
    </Shell>
  ),
};

export const RoomUtilizationError: Story = {
  render: () => (
    <Shell>
      <RoomUtilizationView state={failed} />
    </Shell>
  ),
};

/** K5 (#126 B4): 5 / 5 / 2 / 3 / 3, 40 %. */
export const StudentRetention: Story = {
  render: () => (
    <Shell>
      <StudentRetentionView state={ready(RETENTION)} />
    </Shell>
  ),
};

export const StudentRetentionEmpty: Story = {
  render: () => (
    <Shell>
      <StudentRetentionView state={ready(RETENTION_EMPTY)} />
    </Shell>
  ),
};

export const StudentRetentionError: Story = {
  render: () => (
    <Shell>
      <StudentRetentionView state={failed} />
    </Shell>
  ),
};

/** K6/K7 (#126 B5): T1, T2 and the center. */
export const TutorPerformance: Story = {
  render: () => (
    <Shell>
      <TutorPerformanceView state={ready(TUTORS)} />
    </Shell>
  ),
};

export const TutorPerformanceEmpty: Story = {
  render: () => (
    <Shell>
      <TutorPerformanceView state={ready(TUTORS_EMPTY)} />
    </Shell>
  ),
};

export const TutorPerformanceError: Story = {
  render: () => (
    <Shell>
      <TutorPerformanceView state={failed} />
    </Shell>
  ),
};

/** The whole page for the Owner: seeded queries, no request. */
function OwnerPage() {
  return (
    <StoryAuth session={KPI_OWNER_SESSION}>
      <StoryQueryClient
        data={[
          [BALANCES_KEY, KPI_BALANCES],
          [kpiKeys.revenueTrend({ basis: 'accrual', ...RANGE }), TREND_ACCRUAL],
          [kpiKeys.courseProfitability(RANGE), COURSES],
          [kpiKeys.roomUtilization(RANGE), ROOMS],
          [kpiKeys.studentRetention(RANGE), RETENTION],
          [kpiKeys.tutorPerformance(RANGE), TUTORS],
        ]}
      >
        <StoryRouter
          path={`/reports/kpis?from=${RANGE.from}&to=${RANGE.to}`}
          routes={[
            {
              path: '*',
              element: (
                <main>
                  <KpiPage />
                </main>
              ),
            },
          ]}
        />
      </StoryQueryClient>
    </StoryAuth>
  );
}

export const OwnerPageStory: Story = {
  name: 'Owner page',
  render: () => <OwnerPage />,
  play: async ({ canvas }) => {
    await expect(
      await canvas.findByRole('heading', {
        level: 2,
        name: i18n.t('reports.kpis.tutors.title'),
      }),
    ).toBeVisible();
  },
};

/** 320 px: tables as cards, no horizontal scroll. */
export const OwnerPageMobile: Story = {
  ...narrow,
  name: 'Owner page (320 px)',
  render: () => <OwnerPage />,
  play: async ({ canvas, canvasElement }) => {
    await canvas.findByRole('heading', {
      level: 2,
      name: i18n.t('reports.kpis.tutors.title'),
    });
    const view = canvasElement.ownerDocument.defaultView!;
    const root = canvasElement.ownerDocument.documentElement;
    await expect(root.scrollWidth).toBeLessThanOrEqual(view.innerWidth);
  },
};
