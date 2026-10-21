import type { Meta, StoryObj } from '@storybook/react-vite';
import { expect, fn } from 'storybook/test';
import { ApiError } from '@/app/api';
import { i18n } from '@/i18n';
import { CourseTemplateForm } from './CourseTemplateForm';
import { ALGEBRA, SUBJECT_OPTIONS } from './fixtures';

// The course template editor (#64 G3, G4, G7): a new template (empty), a
// stored one (filled), the row errors of the checks, and the server's
// version conflict. No request: every call is a stub.

const meta = {
  title: 'Courses/CourseTemplateForm',
  component: CourseTemplateForm,
  args: {
    subjects: { status: 'ready', items: SUBJECT_OPTIONS },
    onSave: fn(() => Promise.resolve()),
    onReload: fn(() => Promise.resolve()),
  },
  render: (args) => (
    <main>
      <CourseTemplateForm {...args} />
    </main>
  ),
} satisfies Meta<typeof CourseTemplateForm>;

export default meta;
type Story = StoryObj<typeof meta>;

export const Empty: Story = {
  play: async ({ canvas }) => {
    await expect(
      canvas.getByText(i18n.t('courses.templates.snapshotHelp')),
    ).toBeVisible();
    await expect(
      canvas.getByRole('textbox', {
        name: i18n.t('courses.templates.moduleTitleLabel', { number: 1 }),
      }),
    ).toHaveValue('');
    await expect(
      canvas.getByText(i18n.t('courses.templates.noAssessments')),
    ).toBeVisible();
  },
};

export const Filled: Story = {
  args: { template: ALGEBRA },
  play: async ({ canvas }) => {
    await expect(
      canvas.getByRole('textbox', {
        name: i18n.t('courses.templates.moduleTitleLabel', { number: 2 }),
      }),
    ).toHaveValue('Funciones');
    await expect(
      canvas.getByRole('button', {
        name: i18n.t('courses.templates.moveModuleUpLabel', { number: 1 }),
      }),
    ).toBeDisabled();
  },
};

export const RowErrors: Story = {
  args: { template: { ...ALGEBRA, name: ' ' } },
  play: async ({ canvas, userEvent, args }) => {
    const title = canvas.getByRole('textbox', {
      name: i18n.t('courses.templates.moduleTitleLabel', { number: 2 }),
    });
    await userEvent.clear(title);
    await userEvent.click(
      canvas.getByRole('button', { name: i18n.t('courses.templates.save') }),
    );
    await expect(title).toHaveAttribute('aria-invalid', 'true');
    await expect(
      canvas.getAllByText(i18n.t('courses.templates.validation.titleRequired'))
        .length,
    ).toBeGreaterThan(0);
    await expect(args.onSave).not.toHaveBeenCalled();
  },
};

export const VersionConflict: Story = {
  args: {
    template: ALGEBRA,
    onSave: fn(() =>
      Promise.reject(
        new ApiError({
          code: 'version_conflict',
          status: 409,
          requestId: 'req-story',
        }),
      ),
    ),
  },
  play: async ({ canvas, userEvent }) => {
    await userEvent.click(
      canvas.getByRole('button', { name: i18n.t('courses.templates.save') }),
    );
    await expect(
      await canvas.findByRole('button', {
        name: i18n.t('courses.templates.reload'),
      }),
    ).toBeVisible();
  },
};
