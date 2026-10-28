import type { Meta, StoryObj } from '@storybook/react-vite';
import { expect, fn, userEvent, within } from 'storybook/test';
import { ApiError } from '@/app/api';
import { i18n } from '@/i18n';
import type { LocalDate } from '@/lib/time';
import { StoryRouter } from '@/test/StoryRouter';
import { BillingSettingsDialog } from './BillingSettingsDialog';
import { SETTINGS_OFF, SETTINGS_ON } from './fixtures';

// The "Change billing" dialog (#366 E2): turning billing on (no "Turn
// billing off" while it is off), changing the date while on (with the
// option to turn it off), the past-date warning, a server 422 on the date
// and a 409 with Reload. No request: every call is a stub. The dialog is
// portalled to <body>. axe runs on each story in both themes.

const TODAY = '2026-10-20' as LocalDate;

const meta = {
  title: 'Billing/BillingSettingsDialog',
  component: BillingSettingsDialog,
  args: {
    open: true,
    settings: SETTINGS_OFF,
    today: TODAY,
    onSubmit: fn(() => Promise.resolve()),
    onReload: fn(() => Promise.resolve()),
    onClose: fn(),
  },
  render: (args) => (
    <StoryRouter
      path="/billing/settings"
      routes={[{ path: '*', element: <BillingSettingsDialog {...args} /> }]}
    />
  ),
} satisfies Meta<typeof BillingSettingsDialog>;

export default meta;
type Story = StoryObj<typeof meta>;

type Context = Parameters<NonNullable<Story['play']>>[0];

const dialog = (context: Context) =>
  within(context.canvasElement.ownerDocument.body).getByRole('dialog', {
    name: i18n.t('billing.settings.dialog.title'),
  });

const dateField = (context: Context) =>
  within(dialog(context)).getByLabelText(
    i18n.t('billing.settings.dialog.startsOn'),
  );

export const TurnOn: Story = {
  play: async (context) => {
    await expect(dateField(context)).toHaveValue(TODAY);
    await expect(within(dialog(context)).queryByRole('radio')).toBeNull();
  },
};

export const WhileOn: Story = {
  args: { settings: SETTINGS_ON },
  play: async (context) => {
    await expect(dateField(context)).toHaveValue('2026-09-01');
    await expect(
      within(dialog(context)).getByRole('radio', {
        name: i18n.t('billing.settings.dialog.modes.off'),
      }),
    ).not.toBeChecked();
  },
};

export const TurnOff: Story = {
  args: {
    settings: SETTINGS_ON,
    initialValues: { mode: 'off', startsOn: '2026-09-01' },
  },
  play: async (context) => {
    await expect(
      within(dialog(context)).getByRole('radio', {
        name: i18n.t('billing.settings.dialog.modes.off'),
      }),
    ).toBeChecked();
    await expect(
      within(dialog(context)).queryByLabelText(
        i18n.t('billing.settings.dialog.startsOn'),
      ),
    ).toBeNull();
  },
};

export const PastDateWarning: Story = {
  args: { initialValues: { mode: 'on', startsOn: '2026-10-01' } },
  play: async (context) => {
    await expect(
      within(dialog(context)).getByText(
        i18n.t('billing.settings.dialog.pastWarning'),
      ),
    ).toBeVisible();
    await expect(dateField(context)).not.toHaveAttribute('aria-invalid');
  },
};

export const ServerDateError: Story = {
  args: {
    initialValues: { mode: 'on', startsOn: '2026-10-25' },
    initialError: 'errors:date_out_of_range',
  },
  play: async (context) => {
    await expect(dateField(context)).toHaveAttribute('aria-invalid', 'true');
    await expect(
      within(dialog(context)).getByText(
        i18n.t('date_out_of_range', { ns: 'errors' }),
      ),
    ).toBeVisible();
  },
};

export const VersionConflict: Story = {
  args: {
    initialValues: { mode: 'on', startsOn: '2026-11-01' },
    onSubmit: fn(() =>
      Promise.reject(
        new ApiError({
          code: 'version_conflict',
          status: 409,
          requestId: 'req-billing-settings',
        }),
      ),
    ),
  },
  play: async (context) => {
    await userEvent.click(
      within(dialog(context)).getByRole('button', {
        name: i18n.t('billing.settings.save'),
      }),
    );
    await expect(
      await within(dialog(context)).findByRole('button', {
        name: i18n.t('billing.settings.reload'),
      }),
    ).toBeVisible();
  },
};
