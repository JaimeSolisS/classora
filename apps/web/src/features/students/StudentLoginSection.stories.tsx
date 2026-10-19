import type { Meta, StoryObj } from '@storybook/react-vite';
import { expect, fn, screen, waitFor, within } from 'storybook/test';
import { ApiError } from '@/app/api';
import { i18n } from '@/i18n';
import { LINKED_LOGIN, LOGIN_OPTIONS } from '@/features/consent/fixtures';
import { StoryQueryClient } from '@/test/StoryQueryClient';
import { StudentLoginSection } from './StudentLoginSection';

// The student detail's Login account section (#35 I1, J3): no account, a
// linked account, and the link dialog answering 409
// guardian_consent_required. No request: every call is a stub.

const meta = {
  title: 'Students/StudentLoginSection',
  component: StudentLoginSection,
  args: {
    login: null,
    isMinor: true,
    canManage: true,
    onChange: fn(() => Promise.resolve()),
    searchAccounts: fn(() => Promise.resolve(LOGIN_OPTIONS)),
  },
  render: (args) => (
    <StoryQueryClient>
      <main>
        <StudentLoginSection {...args} />
      </main>
    </StoryQueryClient>
  ),
} satisfies Meta<typeof StudentLoginSection>;

export default meta;
type Story = StoryObj<typeof meta>;

export const NoAccount: Story = {
  play: async ({ canvas }) => {
    await expect(canvas.getByText(i18n.t('students.login.none'))).toBeVisible();
    await expect(
      canvas.getByText(i18n.t('students.login.minorHelp')),
    ).toBeVisible();
  },
};

export const Linked: Story = {
  args: { login: LINKED_LOGIN },
  play: async ({ canvas }) => {
    await expect(canvas.getByText('jose.nunez')).toBeVisible();
    await expect(
      canvas.getByRole('button', { name: i18n.t('students.login.remove') }),
    ).toBeVisible();
  },
};

export const DialogConsentRequired: Story = {
  args: {
    onChange: fn(() =>
      Promise.reject(
        new ApiError({ code: 'guardian_consent_required', status: 409 }),
      ),
    ),
  },
  play: async ({ canvas, userEvent, args }) => {
    await userEvent.click(
      canvas.getByRole('button', { name: i18n.t('students.login.link') }),
    );
    const dialog = within(await screen.findByRole('dialog'));
    await userEvent.type(
      dialog.getByRole('searchbox', { name: i18n.t('students.login.search') }),
      'jose',
    );
    await waitFor(() => expect(args.searchAccounts).toHaveBeenCalled());
    await userEvent.click(
      await dialog.findByRole('radio', { name: /jose\.nunez/ }),
    );
    await userEvent.click(
      dialog.getByRole('button', { name: i18n.t('students.login.linkAction') }),
    );
    await expect(
      await dialog.findByText(i18n.t('errors:guardian_consent_required')),
    ).toBeVisible();
  },
};
