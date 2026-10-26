import type { Meta, StoryObj } from '@storybook/react-vite';
import type { QueryKey } from '@tanstack/react-query';
import { expect, within } from 'storybook/test';
import type { Session } from '@/features/auth/session';
import { studentDetailKey } from '@/features/students/api';
import { JOSE_LIMITED } from '@/features/students/fixtures';
import { i18n } from '@/i18n';
import { StoryAuth } from '@/test/StoryAuth';
import { StoryQueryClient } from '@/test/StoryQueryClient';
import { StoryRouter } from '@/test/StoryRouter';
import {
  approvedListKey,
  workingListKey,
  type ApprovedReportPage,
  type ProgressReportPage,
} from './api';
import {
  APPROVED_PAGE,
  EMPTY_WORKING,
  QUEUE_PAGE,
  STUDENT_ID,
  TUTOR_PAGE,
} from './fixtures';
import { ReportsPage } from './ReportsPage';
import {
  noSideScroll,
  reviewerSession,
  tutorSession,
  VIEWPORTS,
} from './storyKit';
import { StudentReportsPage } from './StudentReportsPage';

// The report lists (#315 H2): the review queue (a reviewer), a tutor's
// list, filtered empty, the empty queue, and the per-student page (working
// and approved reports, "New report"). Every story has 320 and 390 px
// variants that assert the page never scrolls sideways, plus 1280 px for
// the queue. Queries are seeded: no request leaves the story. Axe runs in
// light and dark.

const pages = (page: ProgressReportPage | ApprovedReportPage) => ({
  pages: [page],
  pageParams: [null],
});

const queueKey = workingListKey({
  status: 'submitted',
  kind: null,
  studentId: null,
  mine: false,
});
const allKey = workingListKey({
  status: null,
  kind: null,
  studentId: null,
  mine: false,
});

function ListStory({
  path = '/progress-reports',
  session = reviewerSession,
  data,
  student = false,
}: {
  path?: string;
  session?: Session;
  data: ReadonlyArray<readonly [QueryKey, unknown]>;
  student?: boolean;
}) {
  return (
    <StoryQueryClient data={data}>
      <StoryAuth session={session}>
        <StoryRouter
          path={path}
          routes={[
            {
              path: student
                ? '/students/:studentId/progress-reports'
                : '/progress-reports',
              element: (
                <main className="min-w-0 p-4">
                  {student ? <StudentReportsPage /> : <ReportsPage />}
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
  title: 'Progress reports/Lists',
  component: ListStory,
  args: { data: [[queueKey, pages(QUEUE_PAGE)]] },
  parameters: { viewport: { options: VIEWPORTS } },
} satisfies Meta<typeof ListStory>;

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

export const ReviewQueue: Story = {
  play: async ({ canvasElement }) => {
    await expect(within(canvasElement).getAllByRole('listitem')).toHaveLength(
      2,
    );
  },
};
export const ReviewQueueNarrow320 = at(ReviewQueue, 'narrow320');
export const ReviewQueuePhone390 = at(ReviewQueue, 'phone390');
export const ReviewQueueDesktop1280 = at(ReviewQueue, 'desktop1280');

export const TutorList: Story = {
  args: { session: tutorSession, data: [[allKey, pages(TUTOR_PAGE)]] },
  play: async ({ canvasElement }) => {
    await expect(
      within(canvasElement).getAllByRole('listitem')[0]!,
    ).toHaveTextContent(i18n.t('progressReports.status.draft'));
  },
};
export const TutorListNarrow320 = at(TutorList, 'narrow320');
export const TutorListPhone390 = at(TutorList, 'phone390');

export const FilteredEmpty: Story = {
  args: {
    path: '/progress-reports?kind=weekly',
    data: [
      [
        workingListKey({
          status: 'submitted',
          kind: 'weekly',
          studentId: null,
          mine: false,
        }),
        pages(EMPTY_WORKING),
      ],
    ],
  },
  play: async ({ canvasElement }) => {
    await expect(
      within(canvasElement).getByRole('button', {
        name: i18n.t('progressReports.list.clearFilters'),
      }),
    ).toBeVisible();
  },
};
export const FilteredEmptyNarrow320 = at(FilteredEmpty, 'narrow320');
export const FilteredEmptyPhone390 = at(FilteredEmpty, 'phone390');

export const EmptyQueue: Story = {
  args: { data: [[queueKey, pages(EMPTY_WORKING)]] },
  play: async ({ canvasElement }) => {
    await expect(
      within(canvasElement).getByText(
        i18n.t('progressReports.list.emptyQueue'),
      ),
    ).toBeVisible();
  },
};
export const EmptyQueueNarrow320 = at(EmptyQueue, 'narrow320');
export const EmptyQueuePhone390 = at(EmptyQueue, 'phone390');

export const StudentPage: Story = {
  args: {
    student: true,
    session: tutorSession,
    path: `/students/${STUDENT_ID}/progress-reports`,
    data: [
      [studentDetailKey(STUDENT_ID), JOSE_LIMITED],
      [approvedListKey(STUDENT_ID), pages(APPROVED_PAGE)],
      [
        workingListKey({
          status: null,
          kind: null,
          studentId: STUDENT_ID,
          mine: false,
        }),
        pages(TUTOR_PAGE),
      ],
    ],
  },
  play: async ({ canvasElement }) => {
    await expect(
      within(canvasElement).getByRole('link', {
        name: i18n.t('progressReports.new'),
      }),
    ).toBeVisible();
  },
};
export const StudentPageNarrow320 = at(StudentPage, 'narrow320');
export const StudentPagePhone390 = at(StudentPage, 'phone390');
