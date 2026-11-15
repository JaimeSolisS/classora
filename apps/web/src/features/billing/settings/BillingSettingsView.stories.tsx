import type { Meta, StoryObj } from '@storybook/react-vite';
import { expect, fn, userEvent } from 'storybook/test';
import { i18n } from '@/i18n';
import { StoryAuth } from '@/test/StoryAuth';
import { StoryRouter } from '@/test/StoryRouter';
import { FINANCE_SESSION, VIEWER_SESSION } from '../storySession';
import { BillingSettingsView } from './BillingSettingsView';
import { SETTINGS_OFF, SETTINGS_ON } from './fixtures';

// The Settings tab's card without its query (#366 E2): billing off, on
// from a date, read-only (billing.view only) and loading. No request:
// every call is a stub. axe runs on each story in both themes.

const meta = {
  title: 'Billing/BillingSettingsView',
  component: BillingSettingsView,
  args: {
    state: { status: 'ready', settings: SETTINGS_OFF },
    canManage: true,
    onChange: fn(),
  },
  render: (args) => (
    <StoryAuth session={args.canManage ? FINANCE_SESSION : VIEWER_SESSION}>
      <StoryRouter
        path="/billing/settings"
        routes={[{ path: '*', element: <BillingSettingsView {...args} /> }]}
      />
    </StoryAuth>
  ),
} satisfies Meta<typeof BillingSettingsView>;

export default meta;
type Story = StoryObj<typeof meta>;

export const Off: Story = {
  play: async ({ canvas, args }) => {
    await expect(
      canvas.getByRole('heading', {
        level: 1,
        name: i18n.t('billing.settings.title'),
      }),
    ).toBeVisible();
    await expect(
      canvas.getByRole('link', { name: i18n.t('billing.tabs.settings') }),
    ).toHaveAttribute('aria-current', 'page');
    await expect(
      canvas.getByText(i18n.t('billing.settings.status.off')),
    ).toBeVisible();
    await userEvent.click(
      canvas.getByRole('button', { name: i18n.t('billing.settings.change') }),
    );
    await expect(args.onChange).toHaveBeenCalled();
  },
};

export const On: Story = {
  args: {
    state: { status: 'ready', settings: SETTINGS_ON },
    announcement: i18n.t('billing.settings.saved'),
  },
  play: async ({ canvas }) => {
    await expect(
      canvas.getByRole('region', {
        name: i18n.t('billing.settings.card.title'),
      }),
    ).toBeVisible();
    await expect(canvas.getByRole('status')).toHaveTextContent(
      i18n.t('billing.settings.saved'),
    );
  },
};

export const ReadOnly: Story = {
  args: {
    state: { status: 'ready', settings: SETTINGS_ON },
    canManage: false,
  },
  play: async ({ canvas }) => {
    await expect(
      canvas.queryByRole('button', { name: i18n.t('billing.settings.change') }),
    ).toBeNull();
    await expect(
      canvas.queryByRole('link', { name: i18n.t('billing.tabs.prices') }),
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
