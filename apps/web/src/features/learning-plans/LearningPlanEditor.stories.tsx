import type { Meta, StoryObj } from '@storybook/react-vite';
import { expect, within } from 'storybook/test';
import { ApiError } from '@/app/api';
import { i18n } from '@/i18n';
import { StoryQueryClient } from '@/test/StoryQueryClient';
import { StoryRouter } from '@/test/StoryRouter';
import type { LearningPlan } from './api';
import {
  ACTIVE_PLAN,
  DRAFT_PLAN,
  FULL_PLAN,
  LONG_PLAN,
  MATHS,
  PHYSICS,
  STUDENT_NAME,
} from './fixtures';
import {
  LearningPlanEditor,
  type LearningPlanEditorProps,
} from './LearningPlanEditor';
import type { PlanFailureState } from './PlanFailure';

// The plan editor (#310 G2): create for a tutor (one subject) and for a
// holder (every active subject, one marked "has an active plan"), edit a
// draft, edit an active plan (the note above Save), at the limits (10
// objectives, 50 milestones: every add disabled with its reason), server
// field errors on nested fields, a version conflict, and long unbroken
// text. Every story has 320 and 390 px variants that assert the page never
// scrolls sideways. No request leaves the story. Axe runs in light and dark
// (the storybook projects).

const VIEWPORTS = {
  narrow320: {
    name: 'Narrow (320 px)',
    styles: { width: '320px', height: '640px' },
    type: 'mobile',
  },
  phone390: {
    name: 'Phone (390 px)',
    styles: { width: '390px', height: '844px' },
    type: 'mobile',
  },
} as const;

const never = () => new Promise<never>(() => {});

interface Args {
  plan: LearningPlan | null;
  subjects?: LearningPlanEditorProps['subjects'];
  activeSubjectIds?: ReadonlySet<string>;
  initialFailure?: PlanFailureState | null;
  initialFieldErrors?: unknown;
}

function EditorStory(args: Args) {
  return (
    <StoryQueryClient>
      <StoryRouter
        routes={[
          {
            path: '/',
            element: (
              <main className="min-w-0 p-4">
                <LearningPlanEditor
                  {...args}
                  today="2026-11-12"
                  studentName={STUDENT_NAME}
                  onSave={never}
                  onReload={() => undefined}
                />
              </main>
            ),
          },
        ]}
      />
    </StoryQueryClient>
  );
}

const meta = {
  title: 'LearningPlans/LearningPlanEditor',
  component: EditorStory,
  args: {
    plan: null,
    subjects: [{ id: PHYSICS.id, label: PHYSICS.name }],
  },
  parameters: { viewport: { options: VIEWPORTS } },
} satisfies Meta<typeof EditorStory>;

export default meta;
type Story = StoryObj<typeof meta>;

async function noSideScroll(canvasElement: HTMLElement) {
  const page = canvasElement.ownerDocument.documentElement;
  await expect(page.scrollWidth).toBeLessThanOrEqual(page.clientWidth);
}

const phone = (story: Story, value: 'narrow320' | 'phone390'): Story => ({
  ...story,
  globals: { viewport: { value } },
  play: async (context) => {
    await story.play?.(context);
    await noSideScroll(context.canvasElement);
  },
});

export const CreateTutor: Story = {
  play: async ({ canvasElement }) => {
    const subject = within(canvasElement).getByLabelText(
      i18n.t('learningPlans.editor.subject'),
    );
    await expect(subject.querySelectorAll('option')).toHaveLength(2);
  },
};
export const CreateTutorNarrow320 = phone(CreateTutor, 'narrow320');
export const CreateTutorPhone390 = phone(CreateTutor, 'phone390');

export const CreateHolder: Story = {
  args: {
    subjects: [
      { id: PHYSICS.id, label: 'Física (Secundaria)' },
      { id: MATHS.id, label: 'Matemáticas (Primaria)' },
    ],
    activeSubjectIds: new Set([PHYSICS.id]),
  },
  play: async ({ canvasElement }) => {
    await expect(
      within(canvasElement).getByRole('option', {
        name: i18n.t('learningPlans.editor.subjectWithActive', {
          subject: 'Física (Secundaria)',
        }),
      }),
    ).toBeInTheDocument();
  },
};
export const CreateHolderNarrow320 = phone(CreateHolder, 'narrow320');
export const CreateHolderPhone390 = phone(CreateHolder, 'phone390');

export const EditDraft: Story = {
  args: { plan: DRAFT_PLAN },
  play: async ({ canvasElement }) => {
    await expect(
      within(canvasElement).getByText(
        i18n.t('learningPlans.editor.subjectFixed'),
      ),
    ).toBeVisible();
  },
};
export const EditDraftNarrow320 = phone(EditDraft, 'narrow320');
export const EditDraftPhone390 = phone(EditDraft, 'phone390');

export const EditActive: Story = {
  args: { plan: ACTIVE_PLAN },
  play: async ({ canvasElement }) => {
    await expect(
      within(canvasElement).getByText(
        i18n.t('learningPlans.editor.activeNote', { student: STUDENT_NAME }),
      ),
    ).toBeVisible();
  },
};
export const EditActiveNarrow320 = phone(EditActive, 'narrow320');
export const EditActivePhone390 = phone(EditActive, 'phone390');

export const AtLimits: Story = {
  args: { plan: FULL_PLAN },
  play: async ({ canvasElement }) => {
    const canvas = within(canvasElement);
    await expect(
      canvas.getByRole('button', {
        name: i18n.t('learningPlans.editor.addObjective'),
      }),
    ).toBeDisabled();
    await expect(
      canvas.getByText(
        i18n.t('learningPlans.editor.objectivesAtMax', { max: 10 }),
      ),
    ).toBeVisible();
  },
};
export const AtLimitsNarrow320 = phone(AtLimits, 'narrow320');
export const AtLimitsPhone390 = phone(AtLimits, 'phone390');

export const FieldErrors: Story = {
  args: {
    plan: DRAFT_PLAN,
    initialFieldErrors: new ApiError({
      code: 'validation_error',
      status: 422,
      fieldErrors: [
        { path: ['body', 'title'], code: 'too_long', message: '' },
        {
          path: ['body', 'objectives', 0, 'milestones', 1, 'due_on'],
          code: 'due_on_invalid',
          message: '',
        },
      ],
    }),
  },
  play: async ({ canvasElement }) => {
    await expect(
      await within(canvasElement).findByText(
        i18n.t('learningPlans.validation.dueOnInvalid'),
      ),
    ).toBeVisible();
  },
};
export const FieldErrorsNarrow320 = phone(FieldErrors, 'narrow320');
export const FieldErrorsPhone390 = phone(FieldErrors, 'phone390');

export const VersionConflict: Story = {
  args: {
    plan: DRAFT_PLAN,
    initialFailure: {
      refusal: 'conflict',
      error: new ApiError({ code: 'version_conflict', status: 409 }),
    },
  },
  play: async ({ canvasElement }) => {
    await expect(
      within(canvasElement).getByRole('button', {
        name: i18n.t('academics.reload'),
      }),
    ).toBeVisible();
  },
};
export const VersionConflictNarrow320 = phone(VersionConflict, 'narrow320');
export const VersionConflictPhone390 = phone(VersionConflict, 'phone390');

export const LongText: Story = {
  args: { plan: LONG_PLAN },
};
export const LongTextNarrow320 = phone(LongText, 'narrow320');
export const LongTextPhone390 = phone(LongText, 'phone390');
