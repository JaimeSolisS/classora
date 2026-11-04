import type { Meta, StoryObj } from '@storybook/react-vite';
import type { ReactNode } from 'react';
import { expect, fn } from 'storybook/test';
import { ApiError } from '@/app/api';
import { i18n } from '@/i18n';
import { StoryAuth } from '@/test/StoryAuth';
import { StoryRouter } from '@/test/StoryRouter';
import {
  EXPENSES_OWNER_SESSION,
  MIXED_CATEGORIES,
  TEMPLATE_ENDED,
  TEMPLATES,
  VENDORS,
} from '../fixtures';
import { TemplatesView, type TemplatesState } from './TemplatesView';

// The recurring template list (M7-13b H2): empty, every status, an inactive
// category and vendor, no match, the stale alert, and 320 px. No request.
// axe runs on each story in both themes.

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

const ready = (templates = TEMPLATES): TemplatesState => ({
  status: 'ready',
  templates,
});

function Shell({ children }: { children: ReactNode }) {
  return (
    <StoryAuth session={EXPENSES_OWNER_SESSION}>
      <StoryRouter
        path="/expenses/templates"
        routes={[{ path: '*', element: <main>{children}</main> }]}
      />
    </StoryAuth>
  );
}

const meta = {
  title: 'Finance/Templates',
  component: TemplatesView,
  args: {
    state: ready(),
    params: { statuses: [], categoryId: '', vendorId: '' },
    onFilter: fn(),
    categories: MIXED_CATEGORIES,
    vendors: VENDORS,
    filtered: false,
    canAdd: true,
    onAdd: fn(),
  },
  render: (args) => (
    <Shell>
      <TemplatesView {...args} />
    </Shell>
  ),
} satisfies Meta<typeof TemplatesView>;

export default meta;
type Story = StoryObj<typeof meta>;

/** Active, paused and ended, one with no vendor and one not generated yet. */
export const MixedStatuses: Story = {
  play: async ({ canvas }) => {
    for (const status of ['active', 'paused', 'ended'] as const) {
      await expect(
        canvas.getAllByText(i18n.t(`finance.templates.statuses.${status}`))
          .length,
      ).toBeGreaterThan(0);
    }
  },
};

/** A template whose category and vendor were deactivated since. */
export const InactiveCategory: Story = {
  args: { state: ready([TEMPLATE_ENDED]) },
  play: async ({ canvas }) => {
    await expect(
      canvas.getAllByText(i18n.t('finance.templates.inactiveCategory')).length,
    ).toBeGreaterThan(0);
  },
};

export const Empty: Story = {
  args: { state: ready([]) },
  play: async ({ canvas }) => {
    await expect(
      canvas.getByText(i18n.t('finance.templates.empty')),
    ).toBeVisible();
  },
};

export const NoMatch: Story = {
  args: {
    state: ready([]),
    params: { statuses: ['paused'], categoryId: '', vendorId: '' },
    filtered: true,
  },
};

export const Loading: Story = { args: { state: { status: 'loading' } } };

/** B3: a refetch failed; the rows stay, marked out of date. */
export const RefreshFailed: Story = {
  args: {
    state: {
      ...ready(),
      refreshError: new ApiError({ status: 400, code: 'database_unavailable' }),
      onRefresh: fn(),
    } as TemplatesState,
    addBlocked: true,
  },
  play: async ({ canvas }) => {
    await expect(canvas.getByRole('alert')).toHaveTextContent(
      i18n.t('finance.templates.stale'),
    );
  },
};

/** View only: no "Nueva plantilla". */
export const ViewOnly: Story = { args: { canAdd: false } };

/** Cards below 768 px; nothing scrolls sideways at 320 px. */
export const Mobile: Story = {
  ...narrow,
  play: async ({ canvasElement }) => {
    const view = canvasElement.ownerDocument.defaultView!;
    const root = canvasElement.ownerDocument.documentElement;
    await expect(root.scrollWidth).toBeLessThanOrEqual(view.innerWidth);
  },
};
