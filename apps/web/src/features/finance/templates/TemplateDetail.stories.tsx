import type { Meta, StoryObj } from '@storybook/react-vite';
import type { ReactNode } from 'react';
import { expect, fn } from 'storybook/test';
import { ApiError } from '@/app/api';
import { i18n } from '@/i18n';
import { StoryAuth } from '@/test/StoryAuth';
import { StoryRouter } from '@/test/StoryRouter';
import {
  expensePage,
  EXPENSES_OWNER_SESSION,
  GENERATED_EXPENSES,
  TEMPLATE_ENDED,
  TEMPLATE_FUTURE,
  TEMPLATE_PAUSED,
  TEMPLATE_RENT,
} from '../fixtures';
import { TemplateDetailView } from './TemplateDetailView';

// One recurring template (M7-13b H2): active with generated expenses,
// paused, ended (inactive category and vendor), nothing generated yet, the
// view-only state, the out-of-date state and 320 px. No request. axe runs on
// each story in both themes.

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

function Shell({ children }: { children: ReactNode }) {
  return (
    <StoryAuth session={EXPENSES_OWNER_SESSION}>
      <StoryRouter
        path="/expenses/templates/x"
        routes={[{ path: '*', element: <main>{children}</main> }]}
      />
    </StoryAuth>
  );
}

const meta = {
  title: 'Finance/TemplateDetail',
  component: TemplateDetailView,
  args: {
    template: TEMPLATE_RENT,
    can: { manage: true },
    generated: {
      status: 'ready',
      page: expensePage(GENERATED_EXPENSES, { total: 30 }),
    },
    onPage: fn(),
    onEdit: fn(),
    onPause: fn(),
    onResume: fn(),
    onEnd: fn(),
  },
  render: (args) => (
    <Shell>
      <TemplateDetailView {...args} />
    </Shell>
  ),
} satisfies Meta<typeof TemplateDetailView>;

export default meta;
type Story = StoryObj<typeof meta>;

/** Active, with two generated expenses (one overdue, one paid). */
export const ActiveWithExpenses: Story = {
  play: async ({ canvas }) => {
    await expect(
      canvas.getByRole('heading', {
        name: i18n.t('finance.templates.detail.generated.title'),
      }),
    ).toBeVisible();
  },
};

export const Paused: Story = { args: { template: TEMPLATE_PAUSED } };

/** Ended, on a deactivated category and vendor: only "Editar". */
export const Ended: Story = {
  args: {
    template: TEMPLATE_ENDED,
    generated: { status: 'ready', page: expensePage([]) },
  },
};

export const NoExpensesYet: Story = {
  args: {
    template: TEMPLATE_FUTURE,
    generated: { status: 'ready', page: expensePage([]) },
  },
  play: async ({ canvasElement }) => {
    await expect(
      canvasElement.querySelector('[data-generated-empty]'),
    ).not.toBeNull();
  },
};

export const ViewOnly: Story = { args: { can: { manage: false } } };

/** C3: a refetch failed; the template stays, the actions wait. */
export const RefreshFailed: Story = {
  args: {
    staleError: new ApiError({ status: 400, code: 'database_unavailable' }),
    blocked: true,
    onReload: fn(),
  },
};

export const Mobile: Story = {
  ...narrow,
  play: async ({ canvasElement }) => {
    const view = canvasElement.ownerDocument.defaultView!;
    const root = canvasElement.ownerDocument.documentElement;
    await expect(root.scrollWidth).toBeLessThanOrEqual(view.innerWidth);
  },
};
