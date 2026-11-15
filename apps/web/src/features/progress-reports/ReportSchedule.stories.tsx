import type { Meta, StoryObj } from '@storybook/react-vite';
import { expect, userEvent, within } from 'storybook/test';
import { i18n } from '@/i18n';
import { StoryAuth } from '@/test/StoryAuth';
import { StoryQueryClient } from '@/test/StoryQueryClient';
import { StoryRouter } from '@/test/StoryRouter';
import { schedulesKey, type ReportScheduleList } from './api';
import { STUDENT_ID, STUDENT_NAME } from './fixtures';
import { ReportSchedulePanel } from './ReportSchedulePanel';
import {
  EVERY_REASON,
  EVERY_STATUS,
  NO_SCHEDULES,
  NO_TUTOR,
  SCHEDULES,
} from './scheduleFixtures';
import { noSideScroll, reviewerSession, VIEWPORTS } from './storyKit';

// The "Report schedule" panel (#316 F8): empty, no eligible tutor, the list
// (a draft, a submitted report with its link, a warning, the inactive
// section), every latest-period status, every warning, the add dialog and
// the deactivate dialog. Every story has 320 and 390 px variants that
// assert the page never scrolls sideways. S1 is seeded: no request leaves
// the story. Axe runs in light and dark.

// Wednesday 21 Oct 2026, 12:00 in the center.
const NOW = new Date('2026-10-21T18:00:00Z');

function PanelStory({ data }: { data: ReportScheduleList }) {
  return (
    <StoryQueryClient data={[[schedulesKey(STUDENT_ID), data]]}>
      <StoryAuth session={reviewerSession}>
        <StoryRouter
          path={`/students/${STUDENT_ID}/progress-reports`}
          routes={[
            {
              path: '/students/:studentId/progress-reports',
              element: (
                <main className="min-w-0 p-4">
                  <ReportSchedulePanel
                    studentId={STUDENT_ID}
                    studentName={STUDENT_NAME}
                    now={NOW}
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
  title: 'Progress reports/Report schedule',
  component: PanelStory,
  args: { data: SCHEDULES },
  parameters: { viewport: { options: VIEWPORTS } },
} satisfies Meta<typeof PanelStory>;

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

const body = (canvasElement: HTMLElement) =>
  within(canvasElement.ownerDocument.body);

export const List: Story = {
  play: async ({ canvasElement }) => {
    const canvas = within(canvasElement);
    await expect(
      canvas.getByRole('heading', {
        name: i18n.t('progressReports.schedule.heading'),
      }),
    ).toBeVisible();
    await expect(
      canvas.getByRole('heading', {
        name: i18n.t('progressReports.schedule.inactive'),
      }),
    ).toBeVisible();
    await expect(canvas.getAllByRole('listitem')).toHaveLength(4);
  },
};
export const ListNarrow320 = at(List, 'narrow320');
export const ListPhone390 = at(List, 'phone390');

export const EveryStatus: Story = {
  args: { data: EVERY_STATUS },
  play: async ({ canvasElement }) => {
    await expect(within(canvasElement).getAllByRole('link')).toHaveLength(4);
  },
};
export const EveryStatusNarrow320 = at(EveryStatus, 'narrow320');
export const EveryStatusPhone390 = at(EveryStatus, 'phone390');

export const EveryWarning: Story = {
  args: { data: EVERY_REASON },
  play: async ({ canvasElement }) => {
    await expect(
      within(canvasElement).getByText(
        i18n.t('progressReports.schedule.ineligible.subject_inactive'),
      ),
    ).toBeVisible();
  },
};
export const EveryWarningNarrow320 = at(EveryWarning, 'narrow320');
export const EveryWarningPhone390 = at(EveryWarning, 'phone390');

export const Empty: Story = {
  args: { data: NO_SCHEDULES },
  play: async ({ canvasElement }) => {
    await expect(
      within(canvasElement).getByText(i18n.t('progressReports.schedule.empty')),
    ).toBeVisible();
  },
};
export const EmptyNarrow320 = at(Empty, 'narrow320');
export const EmptyPhone390 = at(Empty, 'phone390');

export const NoEligibleTutor: Story = {
  args: { data: NO_TUTOR },
  play: async ({ canvasElement }) => {
    await expect(
      within(canvasElement).getByRole('button', {
        name: i18n.t('progressReports.schedule.add'),
      }),
    ).toHaveAttribute('aria-disabled', 'true');
  },
};
export const NoEligibleTutorNarrow320 = at(NoEligibleTutor, 'narrow320');
export const NoEligibleTutorPhone390 = at(NoEligibleTutor, 'phone390');

export const AddDialog: Story = {
  args: { data: NO_SCHEDULES },
  play: async ({ canvasElement }) => {
    await userEvent.click(
      within(canvasElement).getByRole('button', {
        name: i18n.t('progressReports.schedule.add'),
      }),
    );
    const dialog = await body(canvasElement).findByRole('dialog');
    await expect(dialog).toBeVisible();
    await expect(
      within(dialog).getByLabelText(i18n.t('progressReports.schedule.tutor')),
    ).toBeVisible();
  },
};
export const AddDialogNarrow320 = at(AddDialog, 'narrow320');
export const AddDialogPhone390 = at(AddDialog, 'phone390');

export const DeactivateDialog: Story = {
  play: async ({ canvasElement }) => {
    const [first] = within(canvasElement).getAllByRole('button', {
      name: new RegExp(`^${i18n.t('progressReports.schedule.deactivate')}:`),
    });
    await userEvent.click(first!);
    const dialog = await body(canvasElement).findByRole('dialog');
    await expect(dialog).toHaveTextContent(
      i18n.t('progressReports.schedule.deactivateText'),
    );
  },
};
export const DeactivateDialogNarrow320 = at(DeactivateDialog, 'narrow320');
export const DeactivateDialogPhone390 = at(DeactivateDialog, 'phone390');
