import type { Meta, StoryObj } from '@storybook/react-vite';
import { expect, fn, screen, within } from 'storybook/test';
import { ApiError } from '@/app/api';
import { i18n } from '@/i18n';
import { ConsentSectionView } from './ConsentSection';
import {
  GUARDIAN_GIVEN,
  GUARDIAN_NOT_GIVEN,
  LAURA_GUARDIAN_ID,
  LAURA_LOGIN_CONSENT,
  STAFF_ADULT,
  STAFF_MINOR,
  STUDENT_ADULT,
} from './fixtures';

// The student detail's Consents section (#35 I2, J3): staff on a minor with
// records and on an adult, a guardian before and after allowing the login,
// an adult student with two guardians, the record dialog with a field error
// and the revoke warning. No request: every call is a stub.

const meta = {
  title: 'Consent/ConsentSectionView',
  component: ConsentSectionView,
  args: {
    overview: STAFF_MINOR,
    mode: 'staff',
    studentName: 'José',
    onRecord: fn(() => Promise.resolve()),
    onRevoke: fn(() =>
      Promise.resolve({
        consent: LAURA_LOGIN_CONSENT,
        student_login_suspended: true,
      }),
    ),
  },
  render: (args) => (
    <main>
      <ConsentSectionView {...args} />
    </main>
  ),
} satisfies Meta<typeof ConsentSectionView>;

export default meta;
type Story = StoryObj<typeof meta>;

export const StaffMinor: Story = {
  play: async ({ canvas }) => {
    await expect(
      canvas.getByRole('heading', { level: 2, name: i18n.t('consent.title') }),
    ).toBeVisible();
    await expect(
      canvas.getByRole('button', { name: i18n.t('consent.record') }),
    ).toBeVisible();
    await expect(canvas.getByText(i18n.t('consent.active'))).toBeVisible();
  },
};

export const StaffAdult: Story = {
  args: { overview: STAFF_ADULT },
  play: async ({ canvas }) => {
    await expect(canvas.getByText(i18n.t('consent.empty'))).toBeVisible();
  },
};

export const GuardianNotGiven: Story = {
  args: { overview: GUARDIAN_NOT_GIVEN, mode: 'guardian' },
  play: async ({ canvas }) => {
    await expect(
      canvas.getByRole('button', {
        name: i18n.t('consent.allowLogin', { name: 'José' }),
      }),
    ).toBeVisible();
  },
};

export const GuardianGiven: Story = {
  args: { overview: GUARDIAN_GIVEN, mode: 'guardian' },
  play: async ({ canvas }) => {
    await expect(
      canvas.getByRole('button', { name: i18n.t('consent.withdraw') }),
    ).toBeVisible();
  },
};

export const StudentTwoGuardians: Story = {
  args: { overview: STUDENT_ADULT, mode: 'student' },
  play: async ({ canvas }) => {
    await expect(
      canvas.getByRole('button', {
        name: `${i18n.t('consent.stopSharing')}: Laura Ñúñez Prieto`,
      }),
    ).toBeVisible();
    await expect(
      canvas.getByRole('button', {
        name: `${i18n.t('consent.share')}: Marta Solís Ruiz`,
      }),
    ).toBeVisible();
  },
};

export const RecordDialogError: Story = {
  args: {
    onRecord: fn(() =>
      Promise.reject(
        new ApiError({ code: 'consent_guardian_invalid', status: 422 }),
      ),
    ),
  },
  play: async ({ canvas, userEvent }) => {
    await userEvent.click(
      canvas.getByRole('button', { name: i18n.t('consent.record') }),
    );
    const dialog = within(await screen.findByRole('dialog'));
    await userEvent.selectOptions(
      dialog.getByRole('combobox', { name: i18n.t('consent.guardian') }),
      LAURA_GUARDIAN_ID,
    );
    await userEvent.click(
      dialog.getByRole('radio', { name: i18n.t('consent.method.paper') }),
    );
    await userEvent.click(
      dialog.getByRole('button', { name: i18n.t('consent.recordAction') }),
    );
    await expect(
      await dialog.findByText(i18n.t('errors:consent_guardian_invalid')),
    ).toBeVisible();
  },
};

export const RevokeWarning: Story = {
  play: async ({ canvas, userEvent }) => {
    await userEvent.click(
      canvas.getByRole('button', {
        name: i18n.t('consent.revokeNamed', {
          scope: i18n.t('consent.scope.student_login'),
          name: 'Laura Ñúñez Prieto',
        }),
      }),
    );
    const dialog = within(await screen.findByRole('dialog'));
    await expect(
      dialog.getByText(i18n.t('consent.revokeWarning', { name: 'José' })),
    ).toBeVisible();
  },
};
