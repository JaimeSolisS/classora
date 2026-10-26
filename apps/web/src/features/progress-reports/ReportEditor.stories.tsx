import type { Meta, StoryObj } from '@storybook/react-vite';
import { expect, within } from 'storybook/test';
import { ApiError } from '@/app/api';
import { i18n } from '@/i18n';
import {
  APPROVED,
  CHANGES_REQUESTED,
  DRAFT,
  FORMER_AUTHOR,
  LONG_TEXT,
  RETRACTED,
  SUBMITTED,
} from './fixtures';
import type { ReportFailureState } from './ReportFailure';
import { ReportStory } from './ReportStory';
import { noSideScroll, VIEWPORTS } from './storyKit';

// The author's report page (#315 H2): a draft with the facts preview, a
// report sent back (the banner), submitted (read only), approved, retracted,
// the former author, field errors, a version conflict and long unbroken
// text. Every story has 320 and 390 px variants that assert the page never
// scrolls sideways. The writes are stubs: no request leaves the story. Axe
// runs in light and dark.

const meta = {
  title: 'Progress reports/Report editor',
  component: ReportStory,
  args: { report: DRAFT },
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

export const DraftWithPreview: Story = {
  play: async ({ canvasElement }) => {
    const canvas = within(canvasElement);
    await expect(
      canvas.getByLabelText(i18n.t('progressReports.fields.summary')),
    ).toBeVisible();
    await expect(
      canvas.getByText(i18n.t('progressReports.facts.preview')),
    ).toBeVisible();
    await expect(
      canvas.getByRole('button', {
        name: i18n.t('progressReports.actions.refreshFacts'),
      }),
    ).toBeVisible();
  },
};
export const DraftWithPreviewNarrow320 = at(DraftWithPreview, 'narrow320');
export const DraftWithPreviewPhone390 = at(DraftWithPreview, 'phone390');

export const ChangesRequested: Story = {
  args: { report: CHANGES_REQUESTED },
  play: async ({ canvasElement }) => {
    const canvas = within(canvasElement);
    await expect(canvas.getByText('Añade los próximos pasos.')).toBeVisible();
    await expect(
      canvas.getByRole('button', {
        name: i18n.t('progressReports.actions.resubmit'),
      }),
    ).toBeVisible();
  },
};
export const ChangesRequestedNarrow320 = at(ChangesRequested, 'narrow320');
export const ChangesRequestedPhone390 = at(ChangesRequested, 'phone390');

export const SubmittedReadOnly: Story = {
  args: { report: SUBMITTED },
  play: async ({ canvasElement }) => {
    await expect(within(canvasElement).queryAllByRole('textbox')).toHaveLength(
      0,
    );
  },
};
export const SubmittedReadOnlyNarrow320 = at(SubmittedReadOnly, 'narrow320');
export const SubmittedReadOnlyPhone390 = at(SubmittedReadOnly, 'phone390');

export const Approved: Story = {
  args: { report: APPROVED },
  play: async ({ canvasElement }) => {
    await expect(
      within(canvasElement).getByRole('link', {
        name: i18n.t('progressReports.read.asFamily'),
      }),
    ).toBeVisible();
  },
};
export const ApprovedNarrow320 = at(Approved, 'narrow320');
export const ApprovedPhone390 = at(Approved, 'phone390');

export const Retracted: Story = {
  args: { report: RETRACTED },
  play: async ({ canvasElement }) => {
    await expect(
      within(canvasElement).getByText(
        i18n.t('progressReports.read.reason', {
          reason: 'Datos de otra alumna.',
        }),
      ),
    ).toBeVisible();
  },
};
export const RetractedNarrow320 = at(Retracted, 'narrow320');
export const RetractedPhone390 = at(Retracted, 'phone390');

export const FormerAuthor: Story = {
  args: { report: FORMER_AUTHOR },
  play: async ({ canvasElement }) => {
    await expect(within(canvasElement).queryAllByRole('button')).toHaveLength(
      0,
    );
  },
};
export const FormerAuthorNarrow320 = at(FormerAuthor, 'narrow320');
export const FormerAuthorPhone390 = at(FormerAuthor, 'phone390');

export const FieldErrors: Story = {
  args: {
    report: { ...DRAFT, summary: null },
    initialErrors: {
      summary: 'progressReports.editor.summaryRequired',
      next_steps: 'errors:too_long',
    },
  },
  play: async ({ canvasElement }) => {
    await expect(
      await within(canvasElement).findByText(
        i18n.t('progressReports.editor.summaryRequired'),
      ),
    ).toBeVisible();
  },
};
export const FieldErrorsNarrow320 = at(FieldErrors, 'narrow320');
export const FieldErrorsPhone390 = at(FieldErrors, 'phone390');

const conflict: ReportFailureState = {
  refusal: { kind: 'conflict' },
  error: new ApiError({
    code: 'version_conflict',
    status: 409,
    requestId: 'story-conflict',
  }),
};

export const VersionConflict: Story = {
  args: { initialFailure: conflict },
  play: async ({ canvasElement }) => {
    await expect(
      within(canvasElement).getByText(i18n.t('progressReports.conflict.edit')),
    ).toBeVisible();
  },
};
export const VersionConflictNarrow320 = at(VersionConflict, 'narrow320');
export const VersionConflictPhone390 = at(VersionConflict, 'phone390');

export const LongText: Story = {
  args: {
    report: {
      ...SUBMITTED,
      summary: LONG_TEXT,
      strengths: LONG_TEXT,
      student: { ...SUBMITTED.student, display_name: 'Maximiliano'.repeat(6) },
    },
  },
};
export const LongTextNarrow320 = at(LongText, 'narrow320');
export const LongTextPhone390 = at(LongText, 'phone390');
