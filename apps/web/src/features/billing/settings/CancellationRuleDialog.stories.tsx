import type { Meta, StoryObj } from '@storybook/react-vite';
import { expect, fn, userEvent, within } from 'storybook/test';
import { ApiError } from '@/app/api';
import { i18n } from '@/i18n';
import type { LocalDate } from '@/lib/time';
import { CancellationRuleDialog } from './CancellationRuleDialog';
import { SEEDED_RULE } from './fixtures';

// "Schedule a change" to the cancellation charges (#385 D2): prefilled
// from the latest version with an empty date (now), a server
// invalid_effective_from on the date and a 409 with Reload. No request:
// every call is a stub. The dialog is portalled to <body>. axe runs on each
// story in both themes.

const TODAY = '2026-10-20' as LocalDate;

const meta = {
  title: 'Billing/CancellationRuleDialog',
  component: CancellationRuleDialog,
  args: {
    open: true,
    latest: SEEDED_RULE,
    today: TODAY,
    timeZone: 'America/Mexico_City',
    onSubmit: fn(() => Promise.resolve()),
    onReload: fn(() => Promise.resolve()),
    onClose: fn(),
  },
} satisfies Meta<typeof CancellationRuleDialog>;

export default meta;
type Story = StoryObj<typeof meta>;

type Context = Parameters<NonNullable<Story['play']>>[0];

const dialog = (context: Context) =>
  within(context.canvasElement.ownerDocument.body).getByRole('dialog', {
    name: i18n.t('billing.settings.cancellation.dialog.title'),
  });

const field = (context: Context, label: string) =>
  within(dialog(context)).getByLabelText(label);

export const Prefilled: Story = {
  play: async (context) => {
    await expect(
      field(context, i18n.t('billing.settings.cancellation.classes.late')),
    ).toHaveValue('charge');
    await expect(
      field(context, i18n.t('billing.settings.cancellation.classes.onTime')),
    ).toHaveValue('credit');
    await expect(
      field(
        context,
        i18n.t('billing.settings.cancellation.dialog.effectiveDate'),
      ),
    ).toHaveValue('');
  },
};

export const ServerDateError: Story = {
  args: {
    initialValues: {
      on_time_outcome: 'credit',
      late_outcome: 'credit',
      no_show_outcome: 'charge',
      effectiveDate: '2026-11-01',
    },
    initialErrors: { effectiveDate: 'errors:invalid_effective_from' },
  },
  play: async (context) => {
    await expect(
      field(
        context,
        i18n.t('billing.settings.cancellation.dialog.effectiveDate'),
      ),
    ).toHaveAttribute('aria-invalid', 'true');
    await expect(
      within(dialog(context)).getByText(
        i18n.t('invalid_effective_from', { ns: 'errors' }),
      ),
    ).toBeVisible();
  },
};

export const VersionConflict: Story = {
  args: {
    onSubmit: fn(() =>
      Promise.reject(
        new ApiError({
          code: 'version_conflict',
          status: 409,
          requestId: 'req-cancellation-rules',
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
