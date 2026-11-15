import type { Meta, StoryObj } from '@storybook/react-vite';
import { expect, fn, within } from 'storybook/test';
import { ApiError } from '@/app/api';
import { i18n } from '@/i18n';
import { StoryRouter } from '@/test/StoryRouter';
import {
  ACTIVE_LEVELS,
  ANA_INACTIVE_LEVEL,
  FIVE_CONTACTS,
  JOSE,
  POSGRADO_REF,
} from './fixtures';
import { EMPTY_STUDENT, valuesOf } from './rules';
import { StudentForm } from './StudentForm';

// The student form (#33 J1, K3, K4): an empty create form, an edit with an
// inactive current level, five contacts, a field error on a contact row and
// the version_conflict alert. No request: every call is a stub.

const LEVELS = ACTIVE_LEVELS.items.map(({ id, name, is_active }) => ({
  id,
  name,
  is_active,
}));

const meta = {
  title: 'Students/StudentForm',
  component: StudentForm,
  args: {
    defaultValues: EMPTY_STUDENT,
    levels: LEVELS,
    today: '2026-10-19',
    cancelTo: '/students',
    labelledBy: 'student-form-title',
    onSubmit: fn(() => Promise.resolve()),
    onReload: fn(() => Promise.resolve()),
  },
  render: (args) => (
    <StoryRouter
      routes={[
        {
          path: '*',
          element: (
            <>
              <h1 id="student-form-title">{i18n.t('students.add')}</h1>
              <StudentForm {...args} />
            </>
          ),
        },
      ]}
    />
  ),
} satisfies Meta<typeof StudentForm>;

export default meta;
type Story = StoryObj<typeof meta>;

const label = (
  key: 'nameLabel' | 'relationshipLabel' | 'phoneLabel',
  n: number,
) => i18n.t(`students.contacts.${key}`, { n });

export const EmptyCreate: Story = {
  play: async ({ canvas }) => {
    await expect(
      canvas.getByRole('form', { name: i18n.t('students.add') }),
    ).toBeVisible();
    await expect(
      canvas.getByRole('group', { name: i18n.t('students.contacts.title') }),
    ).toBeVisible();
    await expect(canvas.getByLabelText(label('nameLabel', 1))).toHaveValue('');
    await expect(
      canvas.getByRole('button', {
        name: i18n.t('students.contacts.removeNamed', { n: 1 }),
      }),
    ).toBeDisabled();
    await expect(
      canvas.getByText(i18n.t('students.contacts.lastOne')),
    ).toBeVisible();
  },
};

export const EditWithInactiveLevel: Story = {
  args: {
    defaultValues: valuesOf(ANA_INACTIVE_LEVEL),
    currentLevel: ANA_INACTIVE_LEVEL.education_level,
  },
  play: async ({ canvas }) => {
    const select = canvas.getByLabelText(i18n.t('students.fields.level'));
    await expect(select).toHaveValue(POSGRADO_REF.id);
    await expect(
      within(select).getByRole('option', {
        name: `Posgrado ${i18n.t('students.fields.levelInactive')}`,
      }),
    ).toBeInTheDocument();
  },
};

export const FiveContacts: Story = {
  args: { defaultValues: valuesOf(FIVE_CONTACTS) },
  play: async ({ canvas }) => {
    await expect(canvas.getByLabelText(label('nameLabel', 5))).toHaveValue(
      'Contacto 5',
    );
    await expect(
      canvas.getByRole('button', { name: i18n.t('students.contacts.add') }),
    ).toBeDisabled();
    await expect(
      canvas.getByText(i18n.t('students.contacts.max')),
    ).toBeVisible();
  },
};

export const ContactRowError: Story = {
  args: { defaultValues: valuesOf(JOSE) },
  play: async ({ canvas, userEvent }) => {
    const phone = canvas.getByLabelText(label('phoneLabel', 2));
    await userEvent.clear(phone);
    await userEvent.type(phone, '12');
    await userEvent.click(
      canvas.getByRole('button', { name: i18n.t('students.save') }),
    );
    await expect(phone).toHaveAttribute('aria-invalid', 'true');
    await expect(phone).toHaveAccessibleDescription(
      i18n.t('students.validation.phoneInvalid'),
    );
  },
};

export const VersionConflict: Story = {
  args: {
    defaultValues: valuesOf(JOSE),
    onSubmit: fn(() =>
      Promise.reject(
        new ApiError({
          code: 'version_conflict',
          status: 409,
          requestId: 'req-story',
        }),
      ),
    ),
  },
  play: async ({ canvas, userEvent, args }) => {
    await userEvent.click(
      canvas.getByRole('button', { name: i18n.t('students.save') }),
    );
    const alert = await canvas.findByRole('alert');
    await expect(alert).toHaveTextContent(i18n.t('errors:version_conflict'));
    await userEvent.click(
      within(alert).getByRole('button', {
        name: i18n.t('students.conflict.reload'),
      }),
    );
    await expect(args.onReload).toHaveBeenCalled();
  },
};
