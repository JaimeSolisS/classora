import type { Meta, StoryObj } from '@storybook/react-vite';
import { expect, within } from 'storybook/test';
import { i18n } from '@/i18n';
import { APPROVED_REVIEW, OWN_SUBMITTED_REVIEWER, REVIEW } from './fixtures';
import { ReportStory } from './ReportStory';
import {
  adminTutorSession,
  noSideScroll,
  reviewerSession,
  VIEWPORTS,
} from './storyKit';

// The review page (#315 H2): a submitted report with every action, each
// dialog open (approve, request changes, retract on an approved report with
// the family warning), self-review (an Administrator author: the actions
// disabled with the reason) and an approved report (retract only). Every
// story has 320 and 390 px variants that assert the page never scrolls
// sideways, plus 1280 px for the submitted report. No request leaves the
// story. Axe runs in light and dark.

const meta = {
  title: 'Progress reports/Report review',
  component: ReportStory,
  args: { report: REVIEW, session: reviewerSession },
  parameters: { viewport: { options: VIEWPORTS } },
} satisfies Meta<typeof ReportStory>;

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

const dialogOpen = async (canvasElement: HTMLElement) =>
  within(canvasElement.ownerDocument.body).findByRole('dialog');

export const SubmittedAllActions: Story = {
  play: async ({ canvasElement }) => {
    const canvas = within(canvasElement);
    for (const key of [
      'progressReports.actions.approve',
      'progressReports.actions.requestChanges',
      'progressReports.actions.retract',
    ] as const) {
      await expect(
        canvas.getByRole('button', { name: i18n.t(key) }),
      ).toBeVisible();
    }
  },
};
export const SubmittedAllActionsNarrow320 = at(
  SubmittedAllActions,
  'narrow320',
);
export const SubmittedAllActionsPhone390 = at(SubmittedAllActions, 'phone390');
export const SubmittedAllActionsDesktop1280 = at(
  SubmittedAllActions,
  'desktop1280',
);

export const ApproveDialog: Story = {
  args: { initialDialog: 'approve' },
  play: async ({ canvasElement }) => {
    await expect(await dialogOpen(canvasElement)).toBeVisible();
  },
};
export const ApproveDialogNarrow320 = at(ApproveDialog, 'narrow320');
export const ApproveDialogPhone390 = at(ApproveDialog, 'phone390');

export const RequestChangesDialog: Story = {
  args: { initialDialog: 'changes' },
  play: async ({ canvasElement }) => {
    const dialog = await dialogOpen(canvasElement);
    await expect(
      within(dialog).getByLabelText(i18n.t('progressReports.review.reason')),
    ).toBeVisible();
  },
};
export const RequestChangesDialogNarrow320 = at(
  RequestChangesDialog,
  'narrow320',
);
export const RequestChangesDialogPhone390 = at(
  RequestChangesDialog,
  'phone390',
);

export const RetractApprovedDialog: Story = {
  args: { report: APPROVED_REVIEW, initialDialog: 'retract' },
  play: async ({ canvasElement }) => {
    const dialog = await dialogOpen(canvasElement);
    await expect(
      within(dialog).getByText(
        i18n.t('progressReports.retract.warning', {
          student: APPROVED_REVIEW.student.display_name,
        }),
      ),
    ).toBeVisible();
  },
};
export const RetractApprovedDialogNarrow320 = at(
  RetractApprovedDialog,
  'narrow320',
);
export const RetractApprovedDialogPhone390 = at(
  RetractApprovedDialog,
  'phone390',
);

export const SelfReviewDisabled: Story = {
  args: { report: OWN_SUBMITTED_REVIEWER, session: adminTutorSession },
  play: async ({ canvasElement }) => {
    const approve = within(canvasElement).getByRole('button', {
      name: i18n.t('progressReports.actions.approve'),
    });
    await expect(approve).toHaveAttribute('aria-disabled', 'true');
    await expect(approve).toHaveAccessibleDescription(
      i18n.t('progressReports.review.selfReview'),
    );
  },
};
export const SelfReviewDisabledNarrow320 = at(SelfReviewDisabled, 'narrow320');
export const SelfReviewDisabledPhone390 = at(SelfReviewDisabled, 'phone390');

export const ApprovedRetractOnly: Story = {
  args: { report: APPROVED_REVIEW },
  play: async ({ canvasElement }) => {
    const canvas = within(canvasElement);
    await expect(
      canvas.queryByRole('button', {
        name: i18n.t('progressReports.actions.approve'),
      }),
    ).toBeNull();
    await expect(
      canvas.getByRole('button', {
        name: i18n.t('progressReports.actions.retract'),
      }),
    ).toBeVisible();
  },
};
export const ApprovedRetractOnlyNarrow320 = at(
  ApprovedRetractOnly,
  'narrow320',
);
export const ApprovedRetractOnlyPhone390 = at(ApprovedRetractOnly, 'phone390');
