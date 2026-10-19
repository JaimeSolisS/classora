import type { Meta, StoryObj } from '@storybook/react-vite';
import { expect, fn, screen, waitFor, within } from 'storybook/test';
import { ApiError } from '@/app/api';
import { LIST_ITEMS as GUARDIAN_ITEMS } from '@/features/guardians/fixtures';
import { i18n } from '@/i18n';
import { StoryQueryClient } from '@/test/StoryQueryClient';
import { StoryRouter } from '@/test/StoryRouter';
import { FamilyDetailView } from './FamilyDetailPage';
import { NUNEZ, NUNEZ_GUARDIAN_VIEW, STUDENT_OPTIONS } from './fixtures';

// The family detail (#172 H3, I2): staff with the management controls, a
// guardian's view (only their own profile, no controls), and the "Add
// student" dialog answering 409 family_member_conflict. No request: every
// call is a stub.

const meta = {
  title: 'Families/FamilyDetailView',
  component: FamilyDetailView,
  args: {
    family: NUNEZ,
    canManage: true,
    onSetActive: fn(() => Promise.resolve()),
    onReload: fn(() => Promise.resolve()),
    onAdd: fn(() => Promise.resolve()),
    onRemove: fn(() => Promise.resolve()),
    searchStudentOptions: fn(() => Promise.resolve(STUDENT_OPTIONS)),
    searchGuardianOptions: fn(() =>
      Promise.resolve(GUARDIAN_ITEMS.slice(0, 2)),
    ),
  },
  render: (args) => (
    <StoryQueryClient>
      <StoryRouter
        routes={[
          {
            path: '*',
            element: (
              <main>
                <FamilyDetailView {...args} />
              </main>
            ),
          },
        ]}
      />
    </StoryQueryClient>
  ),
} satisfies Meta<typeof FamilyDetailView>;

export default meta;
type Story = StoryObj<typeof meta>;

export const Staff: Story = {
  play: async ({ canvas }) => {
    await expect(
      canvas.getByRole('heading', { level: 1, name: NUNEZ.name }),
    ).toBeVisible();
    await expect(
      canvas.getByRole('link', { name: 'José Ñúñez Pérez' }),
    ).toBeVisible();
    await expect(
      canvas.getByRole('link', { name: '+52 00 1234 5678' }),
    ).toHaveAttribute('href', 'tel:+52 00 1234 5678');
    await expect(
      canvas.getByRole('button', { name: i18n.t('families.students.add') }),
    ).toBeVisible();
    await expect(
      canvas.getByRole('button', { name: i18n.t('families.guardians.add') }),
    ).toBeVisible();
  },
};

export const GuardianView: Story = {
  args: { family: NUNEZ_GUARDIAN_VIEW, canManage: false },
  play: async ({ canvas }) => {
    await expect(
      canvas.getByRole('link', { name: 'Laura Ñúñez Prieto' }),
    ).toBeVisible();
    await expect(
      canvas.queryByRole('link', { name: 'Ramón Pérez Solís' }),
    ).toBeNull();
    await expect(canvas.queryByRole('button')).toBeNull();
  },
};

export const AddStudentConflict: Story = {
  args: {
    onAdd: fn(() =>
      Promise.reject(
        new ApiError({ code: 'family_member_conflict', status: 409 }),
      ),
    ),
  },
  play: async ({ canvas, userEvent, args }) => {
    await userEvent.click(
      canvas.getByRole('button', { name: i18n.t('families.students.add') }),
    );
    const dialog = within(await screen.findByRole('dialog'));
    await userEvent.type(
      dialog.getByRole('searchbox', {
        name: i18n.t('families.students.student'),
      }),
      'mateo',
    );
    await waitFor(() => expect(args.searchStudentOptions).toHaveBeenCalled());
    await userEvent.click(
      await dialog.findByRole('radio', { name: 'Ríos Luna, Mateo' }),
    );
    await userEvent.click(
      dialog.getByRole('button', { name: i18n.t('families.addAction') }),
    );
    await expect(
      await dialog.findByText(i18n.t('families.students.conflict')),
    ).toBeVisible();
  },
};
