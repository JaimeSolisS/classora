import type { Meta, StoryObj } from '@storybook/react-vite';
import type { QueryKey } from '@tanstack/react-query';
import { expect, within } from 'storybook/test';
import { FAMILY_STUDENTS_KEY } from '@/features/academics/history/api';
import {
  ADULT_ID,
  CHILD_ID,
  FAMILY_STUDENTS,
} from '@/features/academics/history/fixtures';
import { i18n } from '@/i18n';
import { StoryAuth } from '@/test/StoryAuth';
import { StoryQueryClient } from '@/test/StoryQueryClient';
import { StoryRouter } from '@/test/StoryRouter';
import { approvedListKey, approvedReportKey } from './api';
import { APPROVED_PAGE, APPROVED_REPORT, REPORT_ID } from './fixtures';
import { MyReportPage } from './MyReportPage';
import { MyReportsPage } from './MyReportsPage';
import {
  guardianSession,
  noSideScroll,
  problemResponse,
  stubFetch,
  VIEWPORTS,
} from './storyKit';

// The family pages (#315 H2): the list with the picker, the detail, an
// adult who hasn't shared (ConsentRequired), a report no longer available,
// empty, loading and error. Every story has 320 and 390 px variants that
// assert the page never scrolls sideways. Queries are seeded; the states
// only a response gives stub fetch for the story. Axe runs in light and
// dark.

const pages = (page: unknown) => ({ pages: [page], pageParams: [null] });

function FamilyStory({
  path,
  data,
}: {
  path: string;
  data: ReadonlyArray<readonly [QueryKey, unknown]>;
}) {
  return (
    <StoryQueryClient data={[[FAMILY_STUDENTS_KEY, FAMILY_STUDENTS], ...data]}>
      <StoryAuth session={guardianSession}>
        <StoryRouter
          path={path}
          routes={[
            {
              path: '/my-progress-reports',
              element: (
                <main className="min-w-0 p-4">
                  <MyReportsPage />
                </main>
              ),
            },
            {
              path: '/my-progress-reports/:reportId',
              element: (
                <main className="min-w-0 p-4">
                  <MyReportPage />
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
  title: 'Progress reports/Family',
  component: FamilyStory,
  args: {
    path: `/my-progress-reports?student=${CHILD_ID}`,
    data: [[approvedListKey(CHILD_ID), pages(APPROVED_PAGE)]],
  },
  parameters: { viewport: { options: VIEWPORTS } },
} satisfies Meta<typeof FamilyStory>;

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

const detailPath = `/my-progress-reports/${REPORT_ID}?student=${CHILD_ID}`;

export const ListWithPicker: Story = {
  play: async ({ canvasElement }) => {
    await expect(
      within(canvasElement).getByLabelText(
        i18n.t('progressReports.family.whose'),
      ),
    ).toBeVisible();
  },
};
export const ListWithPickerNarrow320 = at(ListWithPicker, 'narrow320');
export const ListWithPickerPhone390 = at(ListWithPicker, 'phone390');

export const Detail: Story = {
  args: {
    path: detailPath,
    data: [[approvedReportKey(CHILD_ID, REPORT_ID), APPROVED_REPORT]],
  },
  play: async ({ canvasElement }) => {
    await expect(
      within(canvasElement).getByText(
        i18n.t('progressReports.facts.asApproved'),
      ),
    ).toBeVisible();
  },
};
export const DetailNarrow320 = at(Detail, 'narrow320');
export const DetailPhone390 = at(Detail, 'phone390');

export const ConsentRequired: Story = {
  args: { path: `/my-progress-reports?student=${ADULT_ID}`, data: [] },
  play: async ({ canvasElement }) => {
    await expect(
      within(canvasElement).getByText(i18n.t('academics.history.consentTitle')),
    ).toBeVisible();
  },
};
export const ConsentRequiredNarrow320 = at(ConsentRequired, 'narrow320');
export const ConsentRequiredPhone390 = at(ConsentRequired, 'phone390');

export const NoLongerAvailable: Story = {
  args: { path: detailPath, data: [] },
  beforeEach: () =>
    stubFetch((path) =>
      path.endsWith(`/progress-reports/${REPORT_ID}`)
        ? problemResponse(404, 'not_found')
        : null,
    ),
  play: async ({ canvasElement }) => {
    await expect(
      await within(canvasElement).findByText(
        i18n.t('progressReports.family.reportMissing'),
      ),
    ).toBeVisible();
  },
};
export const NoLongerAvailableNarrow320 = at(NoLongerAvailable, 'narrow320');
export const NoLongerAvailablePhone390 = at(NoLongerAvailable, 'phone390');

export const Empty: Story = {
  args: {
    data: [[approvedListKey(CHILD_ID), pages({ ...APPROVED_PAGE, items: [] })]],
  },
  play: async ({ canvasElement }) => {
    await expect(
      within(canvasElement).getByText(i18n.t('progressReports.family.empty')),
    ).toBeVisible();
  },
};
export const EmptyNarrow320 = at(Empty, 'narrow320');
export const EmptyPhone390 = at(Empty, 'phone390');

export const Loading: Story = {
  args: { data: [] },
  beforeEach: () => stubFetch(() => null),
};
export const LoadingNarrow320 = at(Loading, 'narrow320');
export const LoadingPhone390 = at(Loading, 'phone390');

export const LoadError: Story = {
  args: { data: [] },
  beforeEach: () =>
    stubFetch((path) =>
      path.endsWith('/progress-reports')
        ? problemResponse(500, 'internal_error')
        : null,
    ),
  play: async ({ canvasElement }) => {
    await expect(await within(canvasElement).findByRole('alert')).toBeVisible();
  },
};
export const LoadErrorNarrow320 = at(LoadError, 'narrow320');
export const LoadErrorPhone390 = at(LoadError, 'phone390');
