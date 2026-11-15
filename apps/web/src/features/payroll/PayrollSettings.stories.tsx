import type { Meta, StoryObj } from '@storybook/react-vite';
import { expect, fn, within } from 'storybook/test';
import { i18n } from '@/i18n';
import { StoryAuth } from '@/test/StoryAuth';
import { StoryRouter } from '@/test/StoryRouter';
import { SETTINGS_LOCKED, SETTINGS_OFF, SETTINGS_ON } from './fixtures';
import { PayrollSettingsDialog } from './PayrollSettingsDialog';
import { PayrollSettingsView } from './PayrollSettingsView';
import { FINANCE_SESSION } from './storySession';

// The Settings tab and its dialog (#425 H2): off, on, locked, read-only and
// the first-date warning. No request. axe runs on each story in both themes.

const meta = {
  title: 'Payroll/PayrollSettingsView',
  component: PayrollSettingsView,
  args: {
    state: { status: 'ready', settings: SETTINGS_OFF },
    canManage: true,
    onChange: fn(),
  },
  render: (args) => (
    <StoryAuth session={FINANCE_SESSION}>
      <StoryRouter
        path="/payroll/settings"
        routes={[{ path: '*', element: <PayrollSettingsView {...args} /> }]}
      />
    </StoryAuth>
  ),
} satisfies Meta<typeof PayrollSettingsView>;

export default meta;
type Story = StoryObj<typeof meta>;

export const Off: Story = {
  play: async ({ canvas }) => {
    await expect(
      canvas.getByText(i18n.t('payroll.settings.off')),
    ).toBeVisible();
    await expect(
      canvas.getByRole('link', { name: i18n.t('payroll.tabs.settings') }),
    ).toHaveAttribute('aria-current', 'page');
  },
};

export const On: Story = {
  args: {
    state: { status: 'ready', settings: SETTINGS_ON },
    announcement: i18n.t('payroll.settings.saved'),
  },
};

export const Locked: Story = {
  args: { state: { status: 'ready', settings: SETTINGS_LOCKED } },
  play: async ({ canvas }) => {
    await expect(
      canvas.getByText(i18n.t('payroll.settings.startLocked')),
    ).toBeVisible();
  },
};

export const ReadOnly: Story = {
  args: {
    state: { status: 'ready', settings: SETTINGS_ON },
    canManage: false,
  },
  play: async ({ canvas }) => {
    await expect(
      canvas.queryByRole('button', { name: i18n.t('payroll.settings.change') }),
    ).toBeNull();
  },
};

const dialogProps = {
  open: true,
  onSubmit: fn(() => Promise.resolve()),
  onReload: fn(() => Promise.resolve()),
  onClose: fn(),
};

export const DialogFirstDate: Story = {
  render: () => (
    <StoryRouter
      routes={[
        {
          path: '*',
          element: (
            <PayrollSettingsDialog
              {...dialogProps}
              settings={SETTINGS_OFF}
              initialValues={{
                startsOn: '2026-11-02',
                anchor: '2026-01-05',
                basis: 'calendar_days',
              }}
            />
          ),
        },
      ]}
    />
  ),
  play: async ({ canvasElement }) => {
    await expect(
      within(canvasElement.ownerDocument.body).getByText(
        i18n.t('payroll.settings.startWarning'),
      ),
    ).toBeVisible();
  },
};

export const DialogLocked: Story = {
  render: () => (
    <StoryRouter
      routes={[
        {
          path: '*',
          element: (
            <PayrollSettingsDialog
              {...dialogProps}
              settings={SETTINGS_LOCKED}
            />
          ),
        },
      ]}
    />
  ),
  play: async ({ canvasElement }) => {
    await expect(
      within(canvasElement.ownerDocument.body).getByLabelText(
        i18n.t('payroll.settings.anchor'),
      ),
    ).toHaveAttribute('readonly');
  },
};
