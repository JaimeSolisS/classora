import type { Meta, StoryObj } from '@storybook/react-vite';
import { expect, fn, screen, waitFor } from 'storybook/test';
import { ApiError } from '@/app/api';
import { i18n } from '@/i18n';
import { StoryQueryClient } from '@/test/StoryQueryClient';
import { StoryRouter } from '@/test/StoryRouter';
import { ACCOUNT_OPTIONS, LAURA } from './fixtures';
import { GuardianForm } from './GuardianForm';
import { EMPTY_GUARDIAN, valuesOf } from './rules';

// The guardian form (#34 I2, J3): an empty create form, an edit with an
// account, the account picker with an archived result, and an account
// error on the field. No request: the account search is a stub.

const meta = {
  title: 'Guardians/GuardianForm',
  component: GuardianForm,
  args: {
    defaultValues: EMPTY_GUARDIAN,
    cancelTo: '/guardians',
    labelledBy: 'guardian-form-title',
    onSubmit: fn(() => Promise.resolve()),
    onReload: fn(() => Promise.resolve()),
    searchAccounts: fn(() =>
      Promise.resolve({
        items: ACCOUNT_OPTIONS,
        total: ACCOUNT_OPTIONS.length,
      }),
    ),
  },
  render: (args) => (
    <StoryQueryClient>
      <StoryRouter
        routes={[
          {
            path: '*',
            element: (
              <>
                <h1 id="guardian-form-title">{i18n.t('guardians.add')}</h1>
                <GuardianForm {...args} />
              </>
            ),
          },
        ]}
      />
    </StoryQueryClient>
  ),
} satisfies Meta<typeof GuardianForm>;

export default meta;
type Story = StoryObj<typeof meta>;

export const Create: Story = {
  play: async ({ canvas }) => {
    await expect(
      canvas.getByLabelText(i18n.t('guardians.fields.givenNames')),
    ).toBeVisible();
    await expect(
      canvas.getByRole('combobox', {
        name: i18n.t('guardians.account.search'),
      }),
    ).toBeVisible();
  },
};

export const EditWithAccount: Story = {
  args: { defaultValues: valuesOf(LAURA) },
  play: async ({ canvas }) => {
    await expect(canvas.getByText(/laura\.nunez/)).toBeVisible();
    await expect(
      canvas.getByRole('button', { name: i18n.t('guardians.account.remove') }),
    ).toBeVisible();
  },
};

export const AccountPickerWithArchived: Story = {
  play: async ({ canvas, userEvent, args }) => {
    // A click on the empty field lists the accounts (#548), no typing.
    await userEvent.click(
      canvas.getByRole('combobox', {
        name: i18n.t('guardians.account.search'),
      }),
    );
    await waitFor(() => expect(args.searchAccounts).toHaveBeenCalled());
    // The options sit in the portalled list (#545).
    const archived = await screen.findByRole('option', { name: /old\.parent/ });
    await expect(archived).toHaveAttribute('aria-disabled', 'true');
    await userEvent.click(
      screen.getByRole('option', { name: /e2e\.guardians\.parent/ }),
    );
    await expect(
      await canvas.findByRole('button', {
        name: i18n.t('guardians.account.remove'),
      }),
    ).toBeVisible();
  },
};

export const AccountError: Story = {
  args: {
    defaultValues: valuesOf(LAURA),
    onSubmit: fn(() =>
      Promise.reject(
        new ApiError({ code: 'guardian_account_in_use', status: 409 }),
      ),
    ),
  },
  play: async ({ canvas, userEvent }) => {
    await userEvent.click(
      canvas.getByRole('button', { name: i18n.t('guardians.save') }),
    );
    await expect(
      await canvas.findByText(i18n.t('errors:guardian_account_in_use')),
    ).toBeVisible();
  },
};
