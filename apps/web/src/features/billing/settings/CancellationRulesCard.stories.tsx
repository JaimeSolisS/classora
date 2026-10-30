import type { Meta, StoryObj } from '@storybook/react-vite';
import { expect, fn, userEvent, within } from 'storybook/test';
import { i18n } from '@/i18n';
import { StoryAuth } from '@/test/StoryAuth';
import { FINANCE_SESSION, VIEWER_SESSION } from '../storySession';
import { CancellationRulesCard } from './CancellationRulesCard';
import { RULES_SEEDED, RULES_WITH_UPCOMING } from './fixtures';

// The cancellation charges card without its query (#385 D2): only the
// seeded rule, with an upcoming change and the history open, read-only
// (billing.view only) and loading. No request: every call is a stub. axe
// runs on each story in both themes.

const meta = {
  title: 'Billing/CancellationRulesCard',
  component: CancellationRulesCard,
  args: {
    state: { status: 'ready', overview: RULES_SEEDED },
    canManage: true,
    onSchedule: fn(),
    onDelete: fn(() => Promise.resolve()),
  },
  render: (args) => (
    <StoryAuth session={args.canManage ? FINANCE_SESSION : VIEWER_SESSION}>
      <CancellationRulesCard {...args} />
    </StoryAuth>
  ),
} satisfies Meta<typeof CancellationRulesCard>;

export default meta;
type Story = StoryObj<typeof meta>;

export const Seeded: Story = {
  play: async ({ canvas, args }) => {
    await expect(
      canvas.getByRole('region', {
        name: i18n.t('billing.settings.cancellation.title'),
      }),
    ).toBeVisible();
    await expect(
      canvas.getByText(i18n.t('billing.settings.cancellation.excusedAlways')),
    ).toBeVisible();
    await expect(
      canvas.getByText(i18n.t('billing.settings.cancellation.upcoming.empty')),
    ).toBeVisible();
    await userEvent.click(
      canvas.getByRole('button', {
        name: i18n.t('billing.settings.cancellation.schedule'),
      }),
    );
    await expect(args.onSchedule).toHaveBeenCalled();
  },
};

export const WithUpcoming: Story = {
  args: {
    state: { status: 'ready', overview: RULES_WITH_UPCOMING },
    initialHistoryOpen: true,
  },
  play: async ({ canvas, canvasElement, args }) => {
    await expect(
      canvas.getByRole('button', {
        name: i18n.t('billing.settings.cancellation.history.hide'),
      }),
    ).toHaveAttribute('aria-expanded', 'true');
    const remove = canvas.getByRole('button', {
      name: /^Delete|^Eliminar/,
    });
    await userEvent.click(remove);
    const body = within(canvasElement.ownerDocument.body);
    const confirm = await body.findByRole('dialog', {
      name: i18n.t('billing.settings.cancellation.deleteDialog.title'),
    });
    await userEvent.click(
      within(confirm).getByRole('button', {
        name: i18n.t('billing.settings.cancellation.deleteDialog.confirm'),
      }),
    );
    await expect(args.onDelete).toHaveBeenCalled();
  },
};

export const ReadOnly: Story = {
  args: {
    state: { status: 'ready', overview: RULES_WITH_UPCOMING },
    canManage: false,
  },
  play: async ({ canvas }) => {
    await expect(
      canvas.queryByRole('button', {
        name: i18n.t('billing.settings.cancellation.schedule'),
      }),
    ).toBeNull();
    await expect(
      canvas.queryByRole('button', { name: /^Delete|^Eliminar/ }),
    ).toBeNull();
  },
};

export const Loading: Story = {
  args: { state: { status: 'loading' } },
  play: async ({ canvasElement }) => {
    await expect(
      canvasElement.querySelector('[aria-busy="true"]'),
    ).not.toBeNull();
  },
};
