import type { Meta, StoryObj } from '@storybook/react-vite';
import type { ReactNode } from 'react';
import { expect, within } from 'storybook/test';
import { ApiError } from '@/app/api';
import { i18n } from '@/i18n';
import type { LocalDate } from '@/lib/time';
import { StoryQueryClient } from '@/test/StoryQueryClient';
import { StoryRouter } from '@/test/StoryRouter';
import { ANY_SUBJECT, SUBJECT_OPTIONS, TEMPLATE } from '../templates/fixtures';
import type { Evaluation } from './api';
import { EvaluationDetail, type EvaluationActions } from './EvaluationDetail';
import type { FailureState } from './Failure';
import {
  AMENDED,
  CONTEXT,
  COURSE_CONTEXT,
  DRAFT,
  FINAL,
  FORMER_AUTHOR,
  OTHER_TUTORS,
  SCRIPTED,
  STAFF_FINAL,
  STUDENT_ID,
  STUDENT_NAME,
  TODAY,
  VOIDED,
  ZONE,
} from './fixtures';
import { NewEvaluationForm } from './NewEvaluationForm';

// The evaluation form and page (#308 G2): New (no class, a class, a course
// class with the assessment picker), a draft, a final evaluation for its
// author (Amend) and while amending, another tutor's (read-only), a former
// author's, staff's (Void), a voided one, the blocked Finalize's missing
// criteria, a version conflict, the Finalize and Void dialogs, and markup
// shown as text. The phone-first views have 320 and 390 px variants that
// assert the page never scrolls sideways. No request leaves the story.
// Axe runs in light and dark (the storybook projects).

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

const today = TODAY as LocalDate;

function Frame({ children }: { children: ReactNode }) {
  return (
    <StoryQueryClient>
      <StoryRouter
        routes={[
          {
            path: '/',
            element: <main className="min-w-0 p-4">{children}</main>,
          },
        ]}
      />
    </StoryQueryClient>
  );
}

const never = () => new Promise<never>(() => {});
const ACTIONS: EvaluationActions = {
  onSave: never,
  onFinalize: never,
  onDelete: never,
  onVoid: never,
  onReload: () => undefined,
};

interface DetailArgs {
  evaluation: Evaluation;
  initialAmending?: boolean;
  initialDialog?: 'finalize' | 'void' | 'delete' | null;
  initialFailure?: FailureState | null;
}

function Detail(args: DetailArgs) {
  return (
    <Frame>
      <EvaluationDetail {...args} today={today} actions={ACTIONS} />
    </Frame>
  );
}

const meta = {
  title: 'Academics/EvaluationForm',
  component: Detail,
  args: { evaluation: DRAFT },
  parameters: { viewport: { options: VIEWPORTS } },
} satisfies Meta<typeof Detail>;

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

function newForm(context: typeof CONTEXT | null, templateId = TEMPLATE.id) {
  return function NewStory() {
    return (
      <Frame>
        <NewEvaluationForm
          student={{ id: STUDENT_ID, display_name: STUDENT_NAME }}
          context={context}
          templates={{ status: 'ready', items: [TEMPLATE, ANY_SUBJECT] }}
          subjects={SUBJECT_OPTIONS}
          today={today}
          sessionDate={context ? ('2026-11-09' as LocalDate) : null}
          timeZone={ZONE}
          withoutSession={`/students/${STUDENT_ID}/evaluations/new`}
          onCreate={never}
          onReloadTemplates={() => undefined}
          initialTemplateId={templateId}
        />
      </Frame>
    );
  };
}

export const New: Story = {
  render: newForm(null),
  play: async ({ canvasElement }) => {
    const canvas = within(canvasElement);
    await expect(
      canvas.getByRole('heading', {
        level: 1,
        name: i18n.t('academics.evaluations.newTitle'),
      }),
    ).toBeVisible();
    await expect(
      canvas.getByText(i18n.t('academics.evaluations.privateDraft')),
    ).toBeVisible();
    await expect(
      canvas.queryByLabelText(
        i18n.t('academics.evaluations.fields.assessment'),
      ),
    ).toBeNull();
  },
};
export const NewNarrow320 = phone(New, 'narrow320');
export const NewPhone390 = phone(New, 'phone390');

export const NewWithSession: Story = {
  render: newForm(CONTEXT, ANY_SUBJECT.id),
  play: async ({ canvasElement }) => {
    await expect(
      within(canvasElement).getByLabelText(
        i18n.t('academics.evaluations.fields.evaluatedOn'),
      ),
    ).toHaveValue('2026-11-09');
  },
};

export const NewCourseSession: Story = {
  render: newForm(COURSE_CONTEXT),
  play: async ({ canvasElement }) => {
    await expect(
      within(canvasElement).getByLabelText(
        i18n.t('academics.evaluations.fields.assessment'),
      ),
    ).toBeVisible();
  },
};
export const NewCourseSessionNarrow320 = phone(NewCourseSession, 'narrow320');

export const Draft: Story = {
  play: async ({ canvasElement }) => {
    const canvas = within(canvasElement);
    await expect(
      canvas.getByText(i18n.t('academics.evaluations.status.draft')),
    ).toBeVisible();
    await expect(
      canvas.getByRole('button', {
        name: i18n.t('academics.evaluations.actions.finalize'),
      }),
    ).toBeVisible();
  },
};
export const DraftNarrow320 = phone(Draft, 'narrow320');
export const DraftPhone390 = phone(Draft, 'phone390');

export const FinalAuthor: Story = {
  args: { evaluation: AMENDED },
  play: async ({ canvasElement }) => {
    await expect(
      within(canvasElement).getByRole('button', {
        name: i18n.t('academics.evaluations.actions.amend'),
      }),
    ).toBeVisible();
  },
};
export const FinalAuthorNarrow320 = phone(FinalAuthor, 'narrow320');

export const Amending: Story = {
  args: { evaluation: FINAL, initialAmending: true },
  play: async ({ canvasElement }) => {
    await expect(
      within(canvasElement).getByLabelText(
        i18n.t('academics.evaluations.fields.changeReason'),
      ),
    ).toBeVisible();
  },
};
export const AmendingNarrow320 = phone(Amending, 'narrow320');
export const AmendingPhone390 = phone(Amending, 'phone390');

export const FinalReadOnly: Story = {
  args: { evaluation: OTHER_TUTORS },
  play: async ({ canvasElement }) => {
    const canvas = within(canvasElement);
    await expect(canvas.queryAllByRole('button')).toHaveLength(0);
    await expect(
      canvas.getByText(
        i18n.t('academics.evaluations.read.total', {
          total: '17.50',
          max: '20.00',
          percent: '87.50',
        }),
      ),
    ).toBeVisible();
  },
};
export const FinalReadOnlyNarrow320 = phone(FinalReadOnly, 'narrow320');

export const FormerAuthor: Story = {
  args: { evaluation: FORMER_AUTHOR },
  play: async ({ canvasElement }) => {
    await expect(within(canvasElement).queryAllByRole('button')).toHaveLength(
      0,
    );
  },
};

export const StaffCanVoid: Story = {
  args: { evaluation: STAFF_FINAL },
  play: async ({ canvasElement }) => {
    await expect(
      within(canvasElement).getByRole('button', {
        name: i18n.t('academics.evaluations.actions.void'),
      }),
    ).toBeVisible();
  },
};

export const Voided: Story = {
  args: { evaluation: VOIDED },
  play: async ({ canvasElement }) => {
    await expect(
      within(canvasElement).getByText(
        i18n.t('academics.evaluations.status.voided'),
      ),
    ).toBeVisible();
  },
};
export const VoidedNarrow320 = phone(Voided, 'narrow320');

export const IncompleteFinalize: Story = {
  args: {
    evaluation: DRAFT,
    initialFailure: {
      refusal: { kind: 'incomplete', positions: [2] },
      error: null,
    },
  },
  play: async ({ canvasElement }) => {
    await expect(within(canvasElement).getByRole('alert')).toHaveTextContent(
      i18n.t('academics.evaluations.missing'),
    );
  },
};

export const ServerError: Story = {
  args: {
    evaluation: DRAFT,
    initialFailure: {
      refusal: { kind: 'conflict' },
      error: new ApiError({ code: 'version_conflict', status: 409 }),
    },
  },
  play: async ({ canvasElement }) => {
    await expect(within(canvasElement).getByRole('alert')).toHaveTextContent(
      i18n.t('academics.evaluations.conflict'),
    );
  },
};

export const FinalizeDialog: Story = {
  args: { evaluation: DRAFT, initialDialog: 'finalize' },
  play: async () => {
    const dialog = await within(document.body).findByRole('dialog');
    await expect(dialog).toHaveTextContent(
      i18n.t('academics.evaluations.finalize.description', {
        student: STUDENT_NAME,
      }),
    );
  },
};
export const FinalizeDialogNarrow320 = phone(FinalizeDialog, 'narrow320');

export const VoidDialog: Story = {
  args: { evaluation: STAFF_FINAL, initialDialog: 'void' },
  play: async () => {
    const dialog = await within(document.body).findByRole('dialog');
    await expect(
      within(dialog).getByLabelText(
        i18n.t('academics.evaluations.void.reason'),
      ),
    ).toBeVisible();
  },
};
export const VoidDialogNarrow320 = phone(VoidDialog, 'narrow320');

export const MarkupAsText: Story = {
  args: { evaluation: SCRIPTED },
  play: async ({ canvasElement }) => {
    await expect(canvasElement.querySelector('script')).toBeNull();
    await expect(
      within(canvasElement).getAllByText(/<script>/).length,
    ).toBeGreaterThan(0);
  },
};
