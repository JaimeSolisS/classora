import type { Meta, StoryObj } from '@storybook/react-vite';
import type { ParseKeys } from 'i18next';
import {
  QueryClient,
  QueryClientProvider,
  type QueryKey,
} from '@tanstack/react-query';
import { useState, type ReactNode } from 'react';
import { expect, fn, within } from 'storybook/test';
import { ApiError } from '@/app/api';
import type { Session } from '@/features/auth/session';
import { BALANCES_KEY } from '@/features/reporting/api';
import { kpiKeys } from '@/features/reporting/kpis/api';
import {
  COURSES,
  KPI_BALANCES,
  KPI_OWNER_SESSION,
  RETENTION,
  ROOMS,
  TREND_ACCRUAL,
  TUTORS,
} from '@/features/reporting/kpis/fixtures';
import { i18n } from '@/i18n';
import { StoryAuth } from '@/test/StoryAuth';
import { StoryRouter } from '@/test/StoryRouter';
import { LAYOUT_KEY, type DashboardLayout, type WidgetCode } from './api';
import { CustomizeDialog, type DialogEntry } from './CustomizeDialog';
import { DashboardPage } from './DashboardPage';
import { WIDGETS } from './widgets';

// The staff dashboard (M8-10 D3–D5, E1): the Owner's six widgets, Finance's
// five, a layout with every widget hidden, a layout that failed to load
// (the role default, "Customize" disabled), and the "Customize" dialog.
// Every query is seeded: no request. axe runs on each story in both themes.

const RANGE = { from: '2031-03-01', to: '2031-03-31' };

const OWNER_USER =
  KPI_OWNER_SESSION.status === 'signedIn' ? KPI_OWNER_SESSION.user : null;

const FINANCE_SESSION: Session = {
  status: 'signedIn',
  user: {
    ...OWNER_USER!,
    username: 'demo.finance',
    display_name: 'Demo Finance',
    roles: ['finance_staff'],
    permissions: ['finance.report.view', 'schedule.view', 'expense.view'],
  },
};

const ALL: WidgetCode[] = WIDGETS.map((widget) => widget.code);

const layoutOf = (
  codes: readonly WidgetCode[],
  visible = true,
): DashboardLayout => ({
  widgets: codes.map((widget) => ({ widget, visible })),
  customized: !visible,
});

const KPI_DATA: ReadonlyArray<readonly [QueryKey, unknown]> = [
  [BALANCES_KEY, KPI_BALANCES],
  [kpiKeys.revenueTrend({ basis: 'accrual', ...RANGE }), TREND_ACCRUAL],
  [kpiKeys.courseProfitability(RANGE), COURSES],
  [kpiKeys.roomUtilization(RANGE), ROOMS],
  [kpiKeys.studentRetention(RANGE), RETENTION],
  [kpiKeys.tutorPerformance(RANGE), TUTORS],
];

/** A query client with the KPI data and the layout (or its failure). */
function Client({
  layout,
  children,
}: {
  layout: DashboardLayout | Error;
  children: ReactNode;
}) {
  const [client] = useState(() => {
    const queryClient = new QueryClient({
      defaultOptions: {
        queries: { retry: false, retryOnMount: false, staleTime: Infinity },
      },
    });
    for (const [key, value] of KPI_DATA) {
      queryClient.setQueryData(key, value);
    }
    if (layout instanceof Error) {
      queryClient
        .getQueryCache()
        .build(queryClient, { queryKey: LAYOUT_KEY })
        .setState({
          status: 'error',
          error: layout,
          fetchStatus: 'idle',
          errorUpdatedAt: Date.now(),
        });
    } else {
      queryClient.setQueryData(LAYOUT_KEY, layout);
    }
    return queryClient;
  });
  return <QueryClientProvider client={client}>{children}</QueryClientProvider>;
}

function Page({
  session,
  layout,
}: {
  session: Session;
  layout: DashboardLayout | Error;
}) {
  return (
    <StoryAuth session={session}>
      <Client layout={layout}>
        <StoryRouter
          path={`/?from=${RANGE.from}&to=${RANGE.to}`}
          routes={[
            {
              path: '*',
              element: (
                <main>
                  <DashboardPage />
                </main>
              ),
            },
          ]}
        />
      </Client>
    </StoryAuth>
  );
}

const meta = {
  title: 'Dashboard/Dashboard',
  render: () => <></>,
} satisfies Meta;

export default meta;
type Story = StoryObj<typeof meta>;

const heading = (level: 1 | 2, key: ParseKeys<'common'>) => ({
  level,
  name: String(i18n.t(key)),
});

export const Owner: Story = {
  render: () => <Page session={KPI_OWNER_SESSION} layout={layoutOf(ALL)} />,
  play: async ({ canvas }) => {
    await expect(
      await canvas.findByRole('heading', heading(1, 'dashboard.title')),
    ).toBeVisible();
    await expect(
      await canvas.findByRole(
        'heading',
        heading(2, 'reports.kpis.tutors.title'),
      ),
    ).toBeVisible();
  },
};

export const Finance: Story = {
  render: () => (
    <Page
      session={FINANCE_SESSION}
      layout={layoutOf(ALL.filter((code) => code !== 'tutor_performance'))}
    />
  ),
  play: async ({ canvas }) => {
    await expect(
      await canvas.findByRole(
        'heading',
        heading(2, 'reports.kpis.retention.title'),
      ),
    ).toBeVisible();
    await expect(
      canvas.queryByRole('heading', heading(2, 'reports.kpis.tutors.title')),
    ).toBeNull();
  },
};

export const NothingVisible: Story = {
  name: 'Zero visible widgets',
  render: () => (
    <Page session={KPI_OWNER_SESSION} layout={layoutOf(ALL, false)} />
  ),
  play: async ({ canvas }) => {
    await expect(
      await canvas.findByText(i18n.t('dashboard.empty')),
    ).toBeVisible();
    await expect(
      canvas.getByRole('button', { name: i18n.t('dashboard.customize') }),
    ).toBeEnabled();
  },
};

export const LayoutError: Story = {
  name: 'Layout error',
  render: () => (
    <Page
      session={FINANCE_SESSION}
      layout={new ApiError({ status: 400, code: 'database_unavailable' })}
    />
  ),
  play: async ({ canvas }) => {
    await expect(
      await canvas.findByText(i18n.t('dashboard.layoutError')),
    ).toBeVisible();
    await expect(
      canvas.getByRole('button', { name: i18n.t('dashboard.customize') }),
    ).toBeDisabled();
    await expect(
      await canvas.findByRole(
        'heading',
        heading(2, 'reports.kpis.revenueTrend.title'),
      ),
    ).toBeVisible();
  },
};

const ENTRIES = (): DialogEntry[] =>
  WIDGETS.slice(3).map((widget, index) => ({
    widget: widget.code,
    visible: index !== 1,
    title: i18n.t(widget.titleKey),
  }));

export const Dialog: Story = {
  name: 'Customize dialog',
  render: () => (
    <StoryRouter
      path="/"
      routes={[
        {
          path: '*',
          element: (
            <CustomizeDialog
              open
              entries={ENTRIES()}
              onClose={fn()}
              onSave={fn(() => Promise.resolve())}
              onReset={fn(() => Promise.resolve())}
              onUnavailable={fn(() => Promise.resolve(ENTRIES()))}
            />
          ),
        },
      ]}
    />
  ),
  play: async ({ canvasElement, userEvent }) => {
    const body = within(canvasElement.ownerDocument.body);
    const dialog = await body.findByRole('dialog', {
      name: i18n.t('dashboard.dialog.title'),
    });
    const rooms = i18n.t('reports.kpis.rooms.title');
    const up = within(dialog).getByRole('button', {
      name: i18n.t('dashboard.dialog.moveUp', { title: rooms }),
    });
    await expect(up).toBeDisabled();
    await userEvent.click(
      within(dialog).getByRole('button', {
        name: i18n.t('dashboard.dialog.moveDown', { title: rooms }),
      }),
    );
    await expect(
      within(dialog).getByRole('button', {
        name: i18n.t('dashboard.dialog.moveUp', { title: rooms }),
      }),
    ).toBeEnabled();
  },
};

const ALL_ENTRIES = (): DialogEntry[] =>
  WIDGETS.map((widget) => ({
    widget: widget.code,
    visible: true,
    title: i18n.t(widget.titleKey),
  }));

/** E3 (#127 QA D1): at 320 px every row fits; no horizontal scroll. */
export const DialogNarrow: Story = {
  name: 'Customize dialog (320 px)',
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
  render: () => (
    <StoryRouter
      path="/"
      routes={[
        {
          path: '*',
          element: (
            <CustomizeDialog
              open
              entries={ALL_ENTRIES()}
              onClose={fn()}
              onSave={fn(() => Promise.resolve())}
              onReset={fn(() => Promise.resolve())}
              onUnavailable={fn(() => Promise.resolve(ALL_ENTRIES()))}
            />
          ),
        },
      ]}
    />
  ),
  play: async ({ canvasElement }) => {
    const view = canvasElement.ownerDocument.defaultView!;
    await expect(view.innerWidth).toBe(320);
    const body = within(canvasElement.ownerDocument.body);
    const dialog = await body.findByRole('dialog', {
      name: i18n.t('dashboard.dialog.title'),
    });
    await expect(dialog.scrollWidth).toBeLessThanOrEqual(dialog.clientWidth);
    const box = dialog.getBoundingClientRect();
    for (const control of within(dialog).getAllByRole('button')) {
      const rect = control.getBoundingClientRect();
      await expect(rect.left).toBeGreaterThanOrEqual(box.left);
      await expect(rect.right).toBeLessThanOrEqual(box.right);
    }
  },
};
