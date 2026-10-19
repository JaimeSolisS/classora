import type { Meta, StoryObj } from '@storybook/react-vite';
import { expect, fn, screen, waitFor, within } from 'storybook/test';
import { ApiError } from '@/app/api';
import { i18n } from '@/i18n';
import { JOSE } from '@/features/students/fixtures';
import { StoryQueryClient } from '@/test/StoryQueryClient';
import { StoryRouter } from '@/test/StoryRouter';
import { LIST_ITEMS } from './fixtures';
import { StudentGuardiansSection } from './StudentGuardiansSection';

// The student detail's Guardians section (#34 I4, J3): rows, empty, the
// link dialog answering 409 guardian_already_linked, and the unlink
// confirmation. No request: every call is a stub.

const meta = {
  title: 'Guardians/StudentGuardiansSection',
  component: StudentGuardiansSection,
  args: {
    guardians: JOSE.guardians!,
    canManage: true,
    onLink: fn(() => Promise.resolve()),
    onUnlink: fn(() => Promise.resolve()),
    searchGuardians: fn(() => Promise.resolve(LIST_ITEMS.slice(0, 2))),
  },
  render: (args) => (
    <StoryQueryClient>
      <StoryRouter
        routes={[
          {
            path: '*',
            element: (
              <main>
                <StudentGuardiansSection {...args} />
              </main>
            ),
          },
        ]}
      />
    </StoryQueryClient>
  ),
} satisfies Meta<typeof StudentGuardiansSection>;

export default meta;
type Story = StoryObj<typeof meta>;

export const Rows: Story = {
  play: async ({ canvas }) => {
    await expect(
      canvas.getByRole('heading', {
        level: 2,
        name: i18n.t('students.guardians.title'),
      }),
    ).toBeVisible();
    await expect(
      canvas.getByRole('link', { name: '+52 00 1234 5678' }),
    ).toHaveAttribute('href', 'tel:+52 00 1234 5678');
    await expect(
      canvas.getByRole('button', { name: i18n.t('students.guardians.link') }),
    ).toBeVisible();
  },
};

export const Empty: Story = {
  args: { guardians: [] },
  play: async ({ canvas }) => {
    await expect(
      canvas.getByText(i18n.t('students.guardians.empty')),
    ).toBeVisible();
  },
};

export const LinkDialogConflict: Story = {
  args: {
    onLink: fn(() =>
      Promise.reject(
        new ApiError({ code: 'guardian_already_linked', status: 409 }),
      ),
    ),
  },
  play: async ({ canvas, userEvent, args }) => {
    await userEvent.click(
      canvas.getByRole('button', { name: i18n.t('students.guardians.link') }),
    );
    const dialog = within(await screen.findByRole('dialog'));
    await userEvent.type(
      dialog.getByRole('searchbox', {
        name: i18n.t('students.guardians.guardian'),
      }),
      'ramon',
    );
    await waitFor(() => expect(args.searchGuardians).toHaveBeenCalled());
    await userEvent.click(
      await dialog.findByRole('radio', { name: 'Pérez Solís, Ramón' }),
    );
    await userEvent.selectOptions(
      dialog.getByRole('combobox', {
        name: i18n.t('students.guardians.relationship'),
      }),
      'father',
    );
    await userEvent.click(
      dialog.getByRole('button', {
        name: i18n.t('students.guardians.linkAction'),
      }),
    );
    await expect(
      await dialog.findByText(i18n.t('errors:guardian_already_linked')),
    ).toBeVisible();
  },
};

export const UnlinkConfirmation: Story = {
  play: async ({ canvas, userEvent }) => {
    await userEvent.click(
      canvas.getByRole('button', {
        name: i18n.t('students.guardians.unlinkNamed', {
          name: 'Laura Ñúñez Prieto',
        }),
      }),
    );
    const dialog = within(await screen.findByRole('dialog'));
    await expect(
      dialog.getByText(
        i18n.t('students.guardians.unlinkBody', { name: 'Laura Ñúñez Prieto' }),
      ),
    ).toBeVisible();
  },
};
