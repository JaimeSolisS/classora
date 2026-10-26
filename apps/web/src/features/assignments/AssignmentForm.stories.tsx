import type { Meta, StoryObj } from '@storybook/react-vite';
import { expect, userEvent, within } from 'storybook/test';
import { ApiError } from '@/app/api';
import { i18n } from '@/i18n';
import { StoryAuth } from '@/test/StoryAuth';
import { StoryQueryClient } from '@/test/StoryQueryClient';
import { StoryRouter } from '@/test/StoryRouter';
import { pickerKey } from './api';
import { AssignmentForm, type AssignmentFormProps } from './AssignmentForm';
import {
  ANA_ID,
  ASSIGNMENT,
  FRESH,
  MATHS,
  PHYSICS,
  PICKER_PAGE,
  SESSION,
} from './fixtures';
import { noSideScroll, tutorSession, VIEWPORTS } from './storyKit';

// The homework form (#313 G2): create (no class, with the search), create
// from a class, edit (graded: max score locked, recipients locked), the
// field errors of a refused save (recipient_invalid on a checkbox), a
// version conflict, and long unbroken texts. Every story has 320 and 390 px
// variants that assert the page never scrolls sideways. The picker's first
// page is seeded and the saves are stubs: no request leaves the story. Axe
// runs in light and dark.

const TODAY = '2030-11-09';

function FormStory(props: Partial<AssignmentFormProps>) {
  return (
    <StoryQueryClient data={[[pickerKey(''), PICKER_PAGE]]}>
      <StoryAuth session={tutorSession}>
        <StoryRouter
          routes={[
            {
              path: '/',
              element: (
                <main className="min-w-0 p-4">
                  <AssignmentForm
                    assignment={null}
                    subjects={[
                      { id: MATHS.id, label: 'Matemáticas (Secundaria)' },
                      { id: PHYSICS.id, label: 'Física (Secundaria)' },
                    ]}
                    today={TODAY}
                    nowTime="12:00"
                    withoutSessionPath="/assignments/new"
                    onReload={() => undefined}
                    onSave={() => new Promise<void>(() => {})}
                    {...props}
                  />
                </main>
              ),
            },
          ]}
        />
      </StoryAuth>
    </StoryQueryClient>
  );
}

const meta = {
  title: 'Assignments/Form',
  component: FormStory,
  parameters: { viewport: { options: VIEWPORTS } },
} satisfies Meta<typeof FormStory>;

export default meta;
type Story = StoryObj<typeof meta>;

const at = (story: Story, value: keyof typeof VIEWPORTS): Story => ({
  ...story,
  globals: { viewport: { value } },
  play: async (context) => {
    await story.play?.(context);
    await noSideScroll(context.canvasElement);
  },
});

const session = {
  id: SESSION.class_session_id,
  startsAt: SESSION.starts_at,
  subject: { id: MATHS.id, name: MATHS.name, level: 'Secundaria' },
  participants: SESSION.items.map((item) => ({
    id: item.student.id,
    name: item.student.display_name,
  })),
};

export const Create: Story = {
  play: async ({ canvasElement }) => {
    const canvas = within(canvasElement);
    await expect(
      canvas.getByLabelText(i18n.t('assignments.form.search')),
    ).toBeVisible();
    await expect(
      canvas.getByRole('group', { name: i18n.t('assignments.form.students') }),
    ).toBeVisible();
    await expect(canvas.getAllByRole('checkbox')).toHaveLength(3);
  },
};
export const CreateNarrow320 = at(Create, 'narrow320');
export const CreatePhone390 = at(Create, 'phone390');

export const CreateFromClass: Story = {
  args: { session },
  play: async ({ canvasElement }) => {
    const canvas = within(canvasElement);
    await expect(canvas.queryByRole('combobox')).toBeNull();
    for (const box of canvas.getAllByRole('checkbox')) {
      await expect(box).toHaveAttribute('data-state', 'checked');
    }
  },
};
export const CreateFromClassNarrow320 = at(CreateFromClass, 'narrow320');
export const CreateFromClassPhone390 = at(CreateFromClass, 'phone390');

export const EditGraded: Story = {
  args: {
    assignment: ASSIGNMENT,
    participants: session.participants,
  },
  play: async ({ canvasElement }) => {
    const canvas = within(canvasElement);
    await expect(
      canvas.getByLabelText(i18n.t('assignments.form.maxScore')),
    ).toBeDisabled();
    await expect(
      canvas.getAllByText(i18n.t('assignments.form.alreadyHandedIn')),
    ).toHaveLength(3);
  },
};
export const EditGradedNarrow320 = at(EditGraded, 'narrow320');
export const EditGradedPhone390 = at(EditGraded, 'phone390');

export const FieldErrors: Story = {
  args: {
    assignment: FRESH,
    onSave: () =>
      Promise.reject(
        new ApiError({
          code: 'validation_error',
          status: 422,
          fieldErrors: [
            { path: ['body', 'title'], code: 'too_long', message: 'x' },
            {
              path: ['body', 'add_student_ids'],
              code: 'recipient_invalid',
              message: 'x',
            },
          ],
          problem: {
            type: 'about:blank',
            title: 'x',
            status: 422,
            code: 'validation_error',
            request_id: 'story',
            details: { student_ids: [ANA_ID] },
          },
        }),
      ),
  },
  play: async ({ canvasElement }) => {
    const canvas = within(canvasElement);
    await userEvent.click(
      canvas.getByRole('button', { name: i18n.t('assignments.form.save') }),
    );
    await expect(
      await canvas.findByText(
        i18n.t('assignments.validation.recipientInvalid'),
      ),
    ).toBeVisible();
    await expect(
      canvas.getByText(i18n.t('assignments.validation.titleTooLong')),
    ).toBeVisible();
  },
};
export const FieldErrorsNarrow320 = at(FieldErrors, 'narrow320');
export const FieldErrorsPhone390 = at(FieldErrors, 'phone390');

export const VersionConflict: Story = {
  args: {
    assignment: FRESH,
    onSave: () =>
      Promise.reject(new ApiError({ code: 'version_conflict', status: 409 })),
  },
  play: async ({ canvasElement }) => {
    const canvas = within(canvasElement);
    await userEvent.click(
      canvas.getByRole('button', { name: i18n.t('assignments.form.save') }),
    );
    await expect(
      await canvas.findByText(i18n.t('assignments.conflict')),
    ).toBeVisible();
    await expect(
      canvas.getByRole('button', { name: i18n.t('academics.reload') }),
    ).toBeVisible();
  },
};
export const VersionConflictNarrow320 = at(VersionConflict, 'narrow320');
export const VersionConflictPhone390 = at(VersionConflict, 'phone390');

export const LongTexts: Story = {
  args: {
    assignment: {
      ...FRESH,
      title: `Tarea-${'larguísima-'.repeat(15)}`,
      instructions: `Instrucciones-${'sin-espacios-'.repeat(40)}`,
      roster: [
        {
          ...FRESH.roster[0]!,
          student: {
            id: FRESH.roster[0]!.student.id,
            display_name: `Alumno-${'apellido-'.repeat(12)}`,
          },
        },
      ],
    },
  },
  play: async ({ canvasElement }) => {
    await expect(
      within(canvasElement).getByRole('heading', { level: 1 }),
    ).toBeVisible();
  },
};
export const LongTextsNarrow320 = at(LongTexts, 'narrow320');
export const LongTextsPhone390 = at(LongTexts, 'phone390');
