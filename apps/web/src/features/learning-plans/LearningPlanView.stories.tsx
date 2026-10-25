import type { Meta, StoryObj } from '@storybook/react-vite';
import { expect, within } from 'storybook/test';
import { i18n } from '@/i18n';
import { StoryQueryClient } from '@/test/StoryQueryClient';
import { StoryRouter } from '@/test/StoryRouter';
import type { LearningPlan } from './api';
import {
  ACTIVE_PLAN,
  ACTIVE_READ_ONLY,
  COMPLETED_PLAN,
  DISCONTINUED_PLAN,
  DRAFT_PLAN,
  FAMILY_FLAGGED,
  LONG_PLAN,
  STUDENT_NAME,
} from './fixtures';
import {
  LearningPlanView,
  type PlanActions,
  type PlanDialog,
} from './LearningPlanView';

// One learning plan (#310 G2): a holder's draft (every draft action), an
// active plan with an overdue and a done milestone (the progress selects),
// the same for a tutor of another subject (read-only), completed,
// discontinued, the family view (no control even with the flags set), long
// unbroken text, and each dialog open. Every story has 320 and 390 px
// variants that assert the page never scrolls sideways, plus 1280 px for
// the main ones. No request leaves the story. Axe runs in light and dark
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
  desktop1280: {
    name: 'Desktop (1280 px)',
    styles: { width: '1280px', height: '800px' },
    type: 'desktop',
  },
} as const;

const never = () => new Promise<never>(() => {});
const ACTIONS: PlanActions = {
  onTransition: never,
  onDelete: never,
  onProgress: never,
  onReload: () => undefined,
};

interface Args {
  plan: LearningPlan;
  audience?: 'staff' | 'family';
  initialDialog?: PlanDialog | null;
}

function ViewStory({ plan, audience = 'staff', initialDialog = null }: Args) {
  return (
    <StoryQueryClient>
      <StoryRouter
        routes={[
          {
            path: '/',
            element: (
              <main className="min-w-0 p-4">
                <LearningPlanView
                  plan={plan}
                  audience={audience}
                  studentName={STUDENT_NAME}
                  actions={audience === 'staff' ? ACTIONS : undefined}
                  initialDialog={initialDialog}
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
  title: 'LearningPlans/LearningPlanView',
  component: ViewStory,
  args: { plan: DRAFT_PLAN },
  parameters: { viewport: { options: VIEWPORTS } },
} satisfies Meta<typeof ViewStory>;

export default meta;
type Story = StoryObj<typeof meta>;

async function noSideScroll(canvasElement: HTMLElement) {
  const page = canvasElement.ownerDocument.documentElement;
  await expect(page.scrollWidth).toBeLessThanOrEqual(page.clientWidth);
}

const at = (
  story: Story,
  value: 'narrow320' | 'phone390' | 'desktop1280',
): Story => ({
  ...story,
  globals: { viewport: { value } },
  play: async (context) => {
    await story.play?.(context);
    await noSideScroll(context.canvasElement);
  },
});

const progressText = (plan: LearningPlan) =>
  i18n.t('learningPlans.progress', {
    done: plan.progress.done,
    total: plan.progress.total,
    percent: plan.progress.percent,
    count: plan.progress.total,
  });

export const DraftHolder: Story = {
  play: async ({ canvasElement }) => {
    const canvas = within(canvasElement);
    for (const action of ['activate', 'discontinue', 'delete'] as const) {
      await expect(
        canvas.getByRole('button', {
          name: i18n.t(`learningPlans.actions.${action}`),
        }),
      ).toBeVisible();
    }
    await expect(
      canvas.getByRole('link', { name: i18n.t('learningPlans.actions.edit') }),
    ).toBeVisible();
    await expect(canvas.queryByRole('combobox')).toBeNull();
  },
};
export const DraftHolderNarrow320 = at(DraftHolder, 'narrow320');
export const DraftHolderPhone390 = at(DraftHolder, 'phone390');
export const DraftHolderDesktop1280 = at(DraftHolder, 'desktop1280');

export const ActiveWithProgress: Story = {
  args: { plan: ACTIVE_PLAN },
  play: async ({ canvasElement }) => {
    const canvas = within(canvasElement);
    await expect(canvas.getAllByRole('combobox')).toHaveLength(3);
    await expect(
      canvas.getByText(i18n.t('learningPlans.overdue')),
    ).toBeVisible();
    // The text always sits next to the bar.
    await expect(canvas.getByText(progressText(ACTIVE_PLAN))).toBeVisible();
  },
};
export const ActiveWithProgressNarrow320 = at(ActiveWithProgress, 'narrow320');
export const ActiveWithProgressPhone390 = at(ActiveWithProgress, 'phone390');
export const ActiveWithProgressDesktop1280 = at(
  ActiveWithProgress,
  'desktop1280',
);

export const ActiveReadOnly: Story = {
  args: { plan: ACTIVE_READ_ONLY },
  play: async ({ canvasElement }) => {
    const canvas = within(canvasElement);
    await expect(canvas.queryByRole('combobox')).toBeNull();
    await expect(canvas.queryByRole('button')).toBeNull();
  },
};
export const ActiveReadOnlyNarrow320 = at(ActiveReadOnly, 'narrow320');
export const ActiveReadOnlyPhone390 = at(ActiveReadOnly, 'phone390');

export const Completed: Story = {
  args: { plan: COMPLETED_PLAN },
  play: async ({ canvasElement }) => {
    await expect(
      within(canvasElement).getByText(i18n.t('learningPlans.status.completed')),
    ).toBeVisible();
  },
};
export const CompletedNarrow320 = at(Completed, 'narrow320');
export const CompletedPhone390 = at(Completed, 'phone390');

export const Discontinued: Story = {
  args: { plan: DISCONTINUED_PLAN },
  play: async ({ canvasElement }) => {
    await expect(
      within(canvasElement).getByText(
        i18n.t('learningPlans.status.discontinued'),
      ),
    ).toBeVisible();
  },
};
export const DiscontinuedNarrow320 = at(Discontinued, 'narrow320');
export const DiscontinuedPhone390 = at(Discontinued, 'phone390');

export const FamilyReadOnly: Story = {
  args: { plan: FAMILY_FLAGGED, audience: 'family' },
  play: async ({ canvasElement }) => {
    const canvas = within(canvasElement);
    await expect(canvas.queryByRole('button')).toBeNull();
    await expect(canvas.queryByRole('combobox')).toBeNull();
  },
};
export const FamilyReadOnlyNarrow320 = at(FamilyReadOnly, 'narrow320');
export const FamilyReadOnlyPhone390 = at(FamilyReadOnly, 'phone390');

export const LongText: Story = {
  args: { plan: LONG_PLAN },
};
export const LongTextNarrow320 = at(LongText, 'narrow320');
export const LongTextPhone390 = at(LongText, 'phone390');

const dialog = (
  which: PlanDialog,
  plan: LearningPlan,
  title: string,
): Story => ({
  args: { plan, initialDialog: which },
  play: async ({ canvasElement }) => {
    const body = within(canvasElement.ownerDocument.body);
    await expect(
      await body.findByRole('dialog', {
        name: (i18n.t as unknown as (key: string) => string)(title),
      }),
    ).toBeVisible();
  },
});

export const ActivateDialog = dialog(
  'activate',
  DRAFT_PLAN,
  'learningPlans.activate.title',
);
export const ActivateDialogNarrow320 = at(ActivateDialog, 'narrow320');
export const ActivateDialogPhone390 = at(ActivateDialog, 'phone390');
export const CompleteDialog = dialog(
  'complete',
  ACTIVE_PLAN,
  'learningPlans.complete.title',
);
export const CompleteDialogNarrow320 = at(CompleteDialog, 'narrow320');
export const CompleteDialogPhone390 = at(CompleteDialog, 'phone390');
export const DiscontinueDialog = dialog(
  'discontinue',
  ACTIVE_PLAN,
  'learningPlans.discontinue.title',
);
export const DiscontinueDialogNarrow320 = at(DiscontinueDialog, 'narrow320');
export const DiscontinueDialogPhone390 = at(DiscontinueDialog, 'phone390');
export const DeleteDialog = dialog(
  'delete',
  DRAFT_PLAN,
  'learningPlans.delete.title',
);
export const DeleteDialogNarrow320 = at(DeleteDialog, 'narrow320');
export const DeleteDialogPhone390 = at(DeleteDialog, 'phone390');
