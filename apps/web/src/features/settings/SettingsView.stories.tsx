import type { Meta, StoryObj } from '@storybook/react-vite';
import { expect, fn, waitFor } from 'storybook/test';
import { ApiError } from '@/app/api';
import { i18n } from '@/i18n';
import { SETTINGS } from './fixtures';
import { SettingsView } from './SettingsPage';

// The settings page without its query (#37 H7): the form loaded, loading,
// a failed load, the tz_change_requires_migration alert and the hours
// validation error. No request: every call is a stub. axe runs on each
// story in both themes.

const meta = {
  title: 'Settings/SettingsPage',
  component: SettingsView,
  args: {
    settings: SETTINGS,
    error: null,
    onRetry: fn(),
    onSubmit: fn(() => Promise.resolve()),
    onReload: fn(() => Promise.resolve()),
  },
} satisfies Meta<typeof SettingsView>;

export default meta;
type Story = StoryObj<typeof meta>;

const weekday = (key: 'monday' | 'sunday') =>
  i18n.t(`settings.weekdays.${key}`);

export const Loaded: Story = {
  play: async ({ canvas }) => {
    await expect(
      canvas.getByRole('heading', { level: 1, name: i18n.t('settings.title') }),
    ).toBeVisible();
    await expect(
      canvas.getByLabelText(
        i18n.t('settings.hours.dayOpensAt', { day: weekday('monday') }),
      ),
    ).toHaveValue('09:00');
    await expect(
      canvas.getByLabelText(
        i18n.t('settings.hours.dayOpensAt', { day: weekday('sunday') }),
      ),
    ).toBeDisabled();
    await expect(
      canvas.getByLabelText(i18n.t('settings.timeZone.label')),
    ).toHaveAccessibleDescription(i18n.t('settings.timeZone.help'));
  },
};

export const Loading: Story = {
  args: { settings: undefined },
  play: async ({ canvasElement }) => {
    await expect(
      canvasElement.querySelector('[aria-busy="true"]'),
    ).not.toBeNull();
  },
};

export const LoadError: Story = {
  args: {
    settings: undefined,
    error: new ApiError({
      code: 'internal_error',
      status: 500,
      requestId: 'req-settings-0001',
    }),
  },
  play: async ({ canvas, args, userEvent }) => {
    await expect(canvas.getByRole('alert')).toHaveTextContent(
      'req-settings-0001',
    );
    await userEvent.click(
      canvas.getByRole('button', { name: i18n.t('feedback.retry') }),
    );
    await expect(args.onRetry).toHaveBeenCalledOnce();
  },
};

export const TimeZoneChangeBlocked: Story = {
  args: {
    onSubmit: fn(() =>
      Promise.reject(
        new ApiError({
          code: 'tz_change_requires_migration',
          status: 409,
          requestId: 'req-settings-0002',
        }),
      ),
    ),
  },
  play: async ({ canvas, userEvent }) => {
    await userEvent.selectOptions(
      canvas.getByLabelText(i18n.t('settings.timeZone.label')),
      'America/Cancun',
    );
    await userEvent.click(
      canvas.getByRole('button', { name: i18n.t('settings.save') }),
    );
    await expect(await canvas.findByRole('alert')).toHaveTextContent(
      i18n.t('errors:tz_change_requires_migration'),
    );
  },
};

export const HoursValidationError: Story = {
  play: async ({ canvas, args, userEvent }) => {
    const closes = canvas.getByLabelText(
      i18n.t('settings.hours.dayClosesAt', { day: weekday('monday') }),
    );
    await userEvent.clear(closes);
    await userEvent.type(closes, '08:00');
    await userEvent.click(
      canvas.getByRole('button', { name: i18n.t('settings.save') }),
    );
    await waitFor(() =>
      expect(closes).toHaveAccessibleDescription(
        i18n.t('settings.validation.closesAfterOpens'),
      ),
    );
    await expect(closes).toHaveAttribute('aria-invalid', 'true');
    await expect(args.onSubmit).not.toHaveBeenCalled();
  },
};
