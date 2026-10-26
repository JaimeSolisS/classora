import type { Meta, StoryObj } from '@storybook/react-vite';
import type { ParseKeys } from 'i18next';
import { expect, within } from 'storybook/test';
import {
  ADULT_ID,
  CHILD_ID,
  FAMILY_STUDENTS,
} from '@/features/academics/history/fixtures';
import { JOSE } from '@/features/students/fixtures';
import { i18n } from '@/i18n';
import {
  EMPTY_LOG,
  EMPTY_PROGRESS,
  LOG_PAGE,
  MANY_SUBJECTS,
  PROGRESS,
  SINGLE_POINT,
} from './fixtures';
import { ProgressPageStory } from './ProgressStory';
import {
  coordinatorSession,
  guardianSession,
  json,
  noSideScroll,
  problemResponse,
  stubPaths,
  tutorSession,
  VIEWPORTS,
} from './storyKit';

// The progress pages (#322 G2): the family page with data (several
// subjects, plans, an overdue milestone), empty, a single point, more than
// 4 subjects, an adult who hasn't shared (ConsentRequired), not available,
// loading and an error; the staff page with data and the former tutor's
// view. Every story has 320, 390 and 1280 px variants; the narrow ones
// assert the page never scrolls sideways. Reads are stubbed by path. Axe
// runs in light and dark.

const FAMILY_PATH = `/my-progress?student=${CHILD_ID}`;
const STAFF_PATH = `/students/${CHILD_ID}/progress`;

const familyAnswers = (
  progress: unknown = PROGRESS,
  log: unknown = LOG_PAGE,
) => ({
  '/schedule/family-students': json(FAMILY_STUDENTS),
  '/progress': json(progress),
  '/session-log': json(log),
});

const meta = {
  title: 'Progress/Pages',
  component: ProgressPageStory,
  args: { path: FAMILY_PATH, session: guardianSession },
  parameters: { viewport: { options: VIEWPORTS } },
} satisfies Meta<typeof ProgressPageStory>;

export default meta;
type Story = StoryObj<typeof meta>;

const at = (story: Story, value: keyof typeof VIEWPORTS): Story => ({
  ...story,
  globals: { viewport: { value } },
  play: async (context) => {
    await story.play?.(context);
    if (value !== 'desktop1280') {
      await noSideScroll(context.canvasElement);
    }
  },
});

const heading =
  (key: ParseKeys<'common'>) =>
  async ({ canvasElement }: { canvasElement: HTMLElement }) => {
    await expect(
      await within(canvasElement).findByText(
        i18n.t(key),
        {},
        { timeout: 5000 },
      ),
    ).toBeVisible();
  };

export const WithData: Story = {
  beforeEach: () => stubPaths(familyAnswers()),
  play: async ({ canvasElement }) => {
    const canvas = within(canvasElement);
    await expect(
      await canvas.findByRole('heading', {
        name: i18n.t('progress.attendance.heading'),
      }),
    ).toBeVisible();
    await expect(
      canvas.getByRole('heading', { name: i18n.t('progress.log.heading') }),
    ).toBeVisible();
  },
};
export const WithDataNarrow320 = at(WithData, 'narrow320');
export const WithDataPhone390 = at(WithData, 'phone390');
export const WithDataDesktop1280 = at(WithData, 'desktop1280');

export const Empty: Story = {
  beforeEach: () => stubPaths(familyAnswers(EMPTY_PROGRESS, EMPTY_LOG)),
  play: heading('progress.grades.empty'),
};
export const EmptyNarrow320 = at(Empty, 'narrow320');
export const EmptyPhone390 = at(Empty, 'phone390');
export const EmptyDesktop1280 = at(Empty, 'desktop1280');

export const SinglePoint: Story = {
  beforeEach: () => stubPaths(familyAnswers(SINGLE_POINT)),
  play: heading('progress.grades.heading'),
};
export const SinglePointNarrow320 = at(SinglePoint, 'narrow320');
export const SinglePointPhone390 = at(SinglePoint, 'phone390');
export const SinglePointDesktop1280 = at(SinglePoint, 'desktop1280');

export const ManySubjects: Story = {
  beforeEach: () => stubPaths(familyAnswers(MANY_SUBJECTS)),
  play: heading('progress.grades.tooManySubjects'),
};
export const ManySubjectsNarrow320 = at(ManySubjects, 'narrow320');
export const ManySubjectsPhone390 = at(ManySubjects, 'phone390');
export const ManySubjectsDesktop1280 = at(ManySubjects, 'desktop1280');

export const ConsentRequired: Story = {
  args: { path: `/my-progress?student=${ADULT_ID}` },
  beforeEach: () => stubPaths(familyAnswers()),
  play: heading('academics.history.consentTitle'),
};
export const ConsentRequiredNarrow320 = at(ConsentRequired, 'narrow320');
export const ConsentRequiredPhone390 = at(ConsentRequired, 'phone390');
export const ConsentRequiredDesktop1280 = at(ConsentRequired, 'desktop1280');

export const NotAvailable: Story = {
  beforeEach: () =>
    stubPaths({
      ...familyAnswers(),
      '/progress': () => problemResponse(404, 'not_found'),
      '/session-log': () => problemResponse(404, 'not_found'),
    }),
  play: heading('progress.unavailable'),
};
export const NotAvailableNarrow320 = at(NotAvailable, 'narrow320');
export const NotAvailablePhone390 = at(NotAvailable, 'phone390');
export const NotAvailableDesktop1280 = at(NotAvailable, 'desktop1280');

export const Loading: Story = {
  beforeEach: () =>
    stubPaths({ '/schedule/family-students': json(FAMILY_STUDENTS) }),
};
export const LoadingNarrow320 = at(Loading, 'narrow320');
export const LoadingPhone390 = at(Loading, 'phone390');
export const LoadingDesktop1280 = at(Loading, 'desktop1280');

export const LoadError: Story = {
  beforeEach: () =>
    stubPaths({
      ...familyAnswers(),
      '/progress': () => problemResponse(500, 'internal_error'),
    }),
  play: async ({ canvasElement }) => {
    await expect(
      await within(canvasElement).findByRole('alert', {}, { timeout: 5000 }),
    ).toBeVisible();
  },
};
export const LoadErrorNarrow320 = at(LoadError, 'narrow320');
export const LoadErrorPhone390 = at(LoadError, 'phone390');
export const LoadErrorDesktop1280 = at(LoadError, 'desktop1280');

export const StaffWithData: Story = {
  args: { path: STAFF_PATH, session: coordinatorSession },
  beforeEach: () =>
    stubPaths({
      [`/students/${CHILD_ID}`]: json(JOSE),
      '/progress': json(PROGRESS),
      '/session-log': json(LOG_PAGE),
    }),
  play: async ({ canvasElement }) => {
    await expect(
      await within(canvasElement).findByRole(
        'link',
        { name: i18n.t('progress.backToStudent') },
        { timeout: 5000 },
      ),
    ).toBeVisible();
    await heading('progress.log.heading')({ canvasElement });
  },
};
export const StaffWithDataNarrow320 = at(StaffWithData, 'narrow320');
export const StaffWithDataPhone390 = at(StaffWithData, 'phone390');
export const StaffWithDataDesktop1280 = at(StaffWithData, 'desktop1280');

export const FormerTutor: Story = {
  args: { path: STAFF_PATH, session: tutorSession },
  beforeEach: () =>
    stubPaths({
      [`/students/${CHILD_ID}`]: () => problemResponse(404, 'not_found'),
      '/progress': () => problemResponse(404, 'not_found'),
      '/session-log': json(LOG_PAGE),
    }),
  play: heading('progress.formerTutor'),
};
export const FormerTutorNarrow320 = at(FormerTutor, 'narrow320');
export const FormerTutorPhone390 = at(FormerTutor, 'phone390');
export const FormerTutorDesktop1280 = at(FormerTutor, 'desktop1280');
