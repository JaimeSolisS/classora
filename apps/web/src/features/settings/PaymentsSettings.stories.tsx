import type { Meta, StoryObj } from '@storybook/react-vite';
import { expect, fn, waitFor } from 'storybook/test';
import { ApiError } from '@/app/api';
import { noSideScroll, VIEWPORTS } from '@/features/assignments/storyKit';
import { i18n } from '@/i18n';
import { StoryRouter } from '@/test/StoryRouter';
import {
  METHODS_ALL_ACTIVE,
  METHODS_LAST_ACTIVE,
  METHODS_ONE_INACTIVE,
  SETTINGS,
  SETTINGS_WITH_MESSAGES,
} from './fixtures';
import { PaymentsSettingsView } from './PaymentsSettingsPage';

// M8-04b E2: the "Payments and receipts" tab without its queries. The
// methods card: all active, one inactive, the last active disabled, busy and
// a failed change; the message card: empty, filled, a 422 and a conflict;
// and 320 px. No request: every call is a stub. axe runs on each story in
// both themes.

type Args = Parameters<typeof PaymentsSettingsView>[0];

const ready = (methods = METHODS_ALL_ACTIVE) =>
  ({ status: 'ready', methods }) as const;
const settingsReady = (settings = SETTINGS) =>
  ({ status: 'ready', settings }) as const;

const meta = {
  title: 'Settings/PaymentsSettingsPage',
  component: PaymentsSettingsView,
  args: {
    methods: {
      state: ready(),
      onChange: fn(() => Promise.resolve()),
      onReload: fn(() => Promise.resolve()),
    },
    message: {
      state: settingsReady(),
      onSave: fn(() => Promise.resolve()),
      onReload: fn(() => Promise.resolve()),
    },
  },
  parameters: { viewport: { options: VIEWPORTS } },
  // SettingsTabs links need a router.
  render: (args: Args) => (
    <StoryRouter
      path="/settings/payments"
      routes={[{ path: '*', element: <PaymentsSettingsView {...args} /> }]}
    />
  ),
} satisfies Meta<typeof PaymentsSettingsView>;

export default meta;
type Story = StoryObj<typeof meta>;

const method = (code: 'cash' | 'transfer' | 'other') =>
  i18n.t(`billing.paymentMethods.${code}`);
const deactivate = (code: 'cash' | 'transfer' | 'other') =>
  i18n.t('settings.payments.methods.deactivateMethod', {
    method: method(code),
  });
const activate = (code: 'cash' | 'transfer' | 'other') =>
  i18n.t('settings.payments.methods.activateMethod', { method: method(code) });

export const AllActive: Story = {
  play: async ({ canvas }) => {
    await expect(
      canvas.getByRole('heading', {
        level: 1,
        name: i18n.t('settings.payments.title'),
      }),
    ).toBeVisible();
    await expect(
      canvas.getByRole('link', { name: i18n.t('settings.tabs.payments') }),
    ).toHaveAttribute('aria-current', 'page');
    for (const code of ['cash', 'other', 'transfer'] as const) {
      await expect(
        canvas.getByRole('button', { name: deactivate(code) }),
      ).toBeEnabled();
    }
  },
};

export const OneInactive: Story = {
  args: {
    methods: { ...meta.args.methods, state: ready(METHODS_ONE_INACTIVE) },
  },
  play: async ({ canvas }) => {
    await expect(
      canvas.getByRole('button', { name: activate('other') }),
    ).toBeEnabled();
    await expect(
      canvas.getAllByText(i18n.t('catalog.status.inactive')),
    ).toHaveLength(1);
  },
};

export const LastActiveDisabled: Story = {
  args: {
    methods: { ...meta.args.methods, state: ready(METHODS_LAST_ACTIVE) },
  },
  play: async ({ canvas }) => {
    const cash = canvas.getByRole('button', { name: deactivate('cash') });
    await expect(cash).toBeDisabled();
    await expect(cash).toHaveAccessibleDescription(
      i18n.t('settings.payments.methods.lastActive'),
    );
  },
};

export const Busy: Story = {
  args: {
    methods: {
      ...meta.args.methods,
      state: ready(METHODS_ONE_INACTIVE),
      onChange: fn(() => new Promise<void>(() => {})),
    },
  },
  play: async ({ canvas, userEvent }) => {
    const other = canvas.getByRole('button', { name: activate('other') });
    await userEvent.click(other);
    await waitFor(() => expect(other).toHaveAttribute('aria-busy', 'true'));
    await expect(
      canvas.getByRole('button', { name: deactivate('cash') }),
    ).toHaveAttribute('aria-disabled', 'true');
  },
};

export const ChangeFailed: Story = {
  args: {
    methods: {
      ...meta.args.methods,
      state: ready(METHODS_ONE_INACTIVE),
      onChange: fn(() =>
        Promise.reject(
          new ApiError({
            code: 'internal_error',
            status: 500,
            requestId: 'req-methods-0001',
          }),
        ),
      ),
    },
  },
  play: async ({ canvas, userEvent }) => {
    await userEvent.click(
      canvas.getByRole('button', { name: activate('other') }),
    );
    await expect(await canvas.findByRole('alert')).toHaveTextContent(
      'req-methods-0001',
    );
  },
};

export const LoadingMethods: Story = {
  args: {
    methods: { ...meta.args.methods, state: { status: 'loading' } },
    message: { ...meta.args.message, state: { status: 'loading' } },
  },
};

export const MessageEmpty: Story = {
  play: async ({ canvas }) => {
    const es = canvas.getByLabelText(i18n.t('settings.receiptMessage.es'));
    await expect(es).toHaveValue('');
    await expect(
      canvas.getByRole('button', {
        name: i18n.t('settings.receiptMessage.save'),
      }),
    ).toBeDisabled();
  },
};

export const MessageFilled: Story = {
  args: {
    message: {
      ...meta.args.message,
      state: settingsReady(SETTINGS_WITH_MESSAGES),
    },
  },
  play: async ({ canvas }) => {
    await expect(
      canvas.getByLabelText(i18n.t('settings.receiptMessage.en')),
    ).toHaveValue(SETTINGS_WITH_MESSAGES.receipt_message_en);
  },
};

export const MessageRefused: Story = {
  args: {
    message: {
      ...meta.args.message,
      onSave: fn(() =>
        Promise.reject(
          new ApiError({
            code: 'validation_error',
            status: 422,
            fieldErrors: [
              {
                path: ['body', 'receipt_message_es'],
                code: 'value_error',
                message: 'Server message',
              },
            ],
          }),
        ),
      ),
    },
  },
  play: async ({ canvas, userEvent }) => {
    const es = canvas.getByLabelText(i18n.t('settings.receiptMessage.es'));
    await userEvent.type(es, 'Gracias');
    await userEvent.click(
      canvas.getByRole('button', {
        name: i18n.t('settings.receiptMessage.save'),
      }),
    );
    await waitFor(() => expect(es).toHaveAttribute('aria-invalid', 'true'));
    await expect(es).toHaveAccessibleDescription(
      expect.stringContaining(i18n.t('settings.receiptMessage.invalid')),
    );
  },
};

export const MessageConflict: Story = {
  args: {
    message: {
      ...meta.args.message,
      onSave: fn(() =>
        Promise.reject(new ApiError({ code: 'version_conflict', status: 409 })),
      ),
    },
  },
  play: async ({ canvas, args, userEvent }) => {
    await userEvent.type(
      canvas.getByLabelText(i18n.t('settings.receiptMessage.en')),
      'Thanks',
    );
    await userEvent.click(
      canvas.getByRole('button', {
        name: i18n.t('settings.receiptMessage.save'),
      }),
    );
    await waitFor(() => expect(args.message.onReload).toHaveBeenCalledOnce());
    await expect(
      await canvas.findByText(i18n.t('settings.receiptMessage.reloaded')),
    ).toBeVisible();
  },
};

export const Narrow320: Story = {
  args: {
    methods: { ...meta.args.methods, state: ready(METHODS_LAST_ACTIVE) },
    message: {
      ...meta.args.message,
      state: settingsReady(SETTINGS_WITH_MESSAGES),
    },
  },
  globals: { viewport: { value: 'narrow320' } },
  play: async ({ canvasElement }) => {
    await noSideScroll(canvasElement);
  },
};
