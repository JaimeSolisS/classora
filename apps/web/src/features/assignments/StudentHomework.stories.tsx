import type { Meta, StoryObj } from '@storybook/react-vite';
import type { QueryKey } from '@tanstack/react-query';
import { expect, within } from 'storybook/test';
import { FAMILY_STUDENTS_KEY } from '@/features/academics/history/api';
import {
  ADULT_ID,
  CHILD_ID,
  FAMILY_STUDENTS,
  SELF_ONLY,
} from '@/features/academics/history/fixtures';
import type { Session } from '@/features/auth/session';
import { i18n } from '@/i18n';
import { StoryAuth } from '@/test/StoryAuth';
import { StoryQueryClient } from '@/test/StoryQueryClient';
import { StoryRouter } from '@/test/StoryRouter';
import { studentListKey, type StudentAssignment } from './api';
import {
  ASSIGNMENT_ID,
  CLOSED_ITEM,
  FEEDBACK_ITEM,
  GRADED_ITEM,
  MISSING_ITEM,
  PENDING_ITEM,
  STUDENT_ID,
  STUDENT_LIST,
  SUBMITTED_ITEM,
} from './fixtures';
import { MyHomeworkDetailPage } from './MyHomeworkDetailPage';
import { MyHomeworkPage } from './MyHomeworkPage';
import {
  guardianSession,
  problemResponse,
  stubFetch,
  studentSession,
  VIEWPORTS,
  noSideScroll,
} from './storyKit';
import {
  StudentAssignmentView,
  type ViewAudience,
} from './StudentAssignmentView';

// The student and family pages (#313 G2): the student's list with the
// summary, the detail pending (the hand-in form), handed in (replace),
// missing (the late help), handed in after the deadline (closed), graded
// (score, "Updated"), graded with feedback only, the guardian with the
// picker and the guardian's read-only detail, ConsentRequired, "no longer
// available", empty, loading and error. Every story has 320 and 390 px
// variants that assert the page never scrolls sideways. Queries are seeded
// (or fetch is stubbed for a response-only state): no request leaves the
// story. Axe runs in light and dark.

function ListStory({
  path,
  data,
  session,
}: {
  path: string;
  data: ReadonlyArray<readonly [QueryKey, unknown]>;
  session: Session;
}) {
  return (
    <StoryQueryClient data={data}>
      <StoryAuth session={session}>
        <StoryRouter
          path={path}
          routes={[
            {
              path: '/my-homework',
              element: (
                <main className="min-w-0 p-4">
                  <MyHomeworkPage />
                </main>
              ),
            },
            {
              path: '/my-homework/:assignmentId',
              element: (
                <main className="min-w-0 p-4">
                  <MyHomeworkDetailPage />
                </main>
              ),
            },
          ]}
        />
      </StoryAuth>
    </StoryQueryClient>
  );
}

function DetailStory({
  item,
  audience = 'self',
}: {
  item: StudentAssignment;
  audience?: ViewAudience;
}) {
  return (
    <StoryQueryClient>
      <StoryAuth
        session={audience === 'self' ? studentSession : guardianSession}
      >
        <StoryRouter
          routes={[
            {
              path: '/',
              element: (
                <main className="min-w-0 p-4">
                  <StudentAssignmentView
                    item={item}
                    audience={audience}
                    studentName="Lucía Pérez"
                    onSubmit={() => new Promise<void>(() => {})}
                    onReload={() => undefined}
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
  title: 'Assignments/StudentHomework',
  component: DetailStory,
  args: { item: PENDING_ITEM },
  parameters: { viewport: { options: VIEWPORTS } },
} satisfies Meta<typeof DetailStory>;

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

const pages = (list: typeof STUDENT_LIST) => ({
  pages: [list],
  pageParams: [null],
});

export const StudentList: Story = {
  render: () => (
    <ListStory
      path="/my-homework"
      session={studentSession}
      data={[
        [FAMILY_STUDENTS_KEY, SELF_ONLY],
        [studentListKey(STUDENT_ID, null), pages(STUDENT_LIST)],
      ]}
    />
  ),
  play: async ({ canvasElement }) => {
    const canvas = within(canvasElement);
    await expect(canvas.getAllByRole('listitem')).toHaveLength(3);
    await expect(
      canvas.getByRole('group', { name: i18n.t('assignments.summary.label') }),
    ).toBeVisible();
  },
};
export const StudentListNarrow320 = at(StudentList, 'narrow320');
export const StudentListPhone390 = at(StudentList, 'phone390');

export const Pending: Story = {
  play: async ({ canvasElement }) => {
    await expect(
      within(canvasElement).getByRole('button', {
        name: i18n.t('assignments.submit.handIn'),
      }),
    ).toBeVisible();
  },
};
export const PendingNarrow320 = at(Pending, 'narrow320');
export const PendingPhone390 = at(Pending, 'phone390');

export const HandedIn: Story = {
  args: { item: SUBMITTED_ITEM },
  play: async ({ canvasElement }) => {
    await expect(
      within(canvasElement).getByRole('button', {
        name: i18n.t('assignments.submit.replace'),
      }),
    ).toBeVisible();
  },
};
export const HandedInNarrow320 = at(HandedIn, 'narrow320');
export const HandedInPhone390 = at(HandedIn, 'phone390');

export const Missing: Story = {
  args: { item: MISSING_ITEM },
  play: async ({ canvasElement }) => {
    await expect(
      within(canvasElement).getByText(i18n.t('assignments.submit.lateHelp')),
    ).toBeVisible();
  },
};
export const MissingNarrow320 = at(Missing, 'narrow320');
export const MissingPhone390 = at(Missing, 'phone390');

export const Closed: Story = {
  args: { item: CLOSED_ITEM },
  play: async ({ canvasElement }) => {
    await expect(within(canvasElement).queryByRole('textbox')).toBeNull();
  },
};
export const ClosedNarrow320 = at(Closed, 'narrow320');
export const ClosedPhone390 = at(Closed, 'phone390');

export const Graded: Story = {
  args: { item: GRADED_ITEM },
  play: async ({ canvasElement }) => {
    await expect(
      within(canvasElement).getByText(i18n.t('assignments.updated')),
    ).toBeVisible();
  },
};
export const GradedNarrow320 = at(Graded, 'narrow320');
export const GradedPhone390 = at(Graded, 'phone390');

export const FeedbackOnlyGraded: Story = {
  args: { item: FEEDBACK_ITEM },
  play: async ({ canvasElement }) => {
    await expect(
      within(canvasElement).getByText(i18n.t('assignments.feedbackOnly')),
    ).toBeVisible();
  },
};
export const FeedbackOnlyGradedNarrow320 = at(FeedbackOnlyGraded, 'narrow320');
export const FeedbackOnlyGradedPhone390 = at(FeedbackOnlyGraded, 'phone390');

export const GuardianDetail: Story = {
  args: { item: { ...SUBMITTED_ITEM, can_submit: true }, audience: 'guardian' },
  play: async ({ canvasElement }) => {
    await expect(within(canvasElement).queryByRole('textbox')).toBeNull();
  },
};
export const GuardianDetailNarrow320 = at(GuardianDetail, 'narrow320');
export const GuardianDetailPhone390 = at(GuardianDetail, 'phone390');

export const GuardianWithPicker: Story = {
  render: () => (
    <ListStory
      path="/my-homework"
      session={guardianSession}
      data={[
        [FAMILY_STUDENTS_KEY, FAMILY_STUDENTS],
        [studentListKey(CHILD_ID, null), pages(STUDENT_LIST)],
      ]}
    />
  ),
  play: async ({ canvasElement }) => {
    await expect(
      within(canvasElement).getByLabelText(i18n.t('assignments.family.whose')),
    ).toBeVisible();
  },
};
export const GuardianWithPickerNarrow320 = at(GuardianWithPicker, 'narrow320');
export const GuardianWithPickerPhone390 = at(GuardianWithPicker, 'phone390');

export const ConsentRequired: Story = {
  render: () => (
    <ListStory
      path={`/my-homework?student=${ADULT_ID}`}
      session={guardianSession}
      data={[[FAMILY_STUDENTS_KEY, FAMILY_STUDENTS]]}
    />
  ),
  play: async ({ canvasElement }) => {
    await expect(
      within(canvasElement).getByRole('heading', {
        name: i18n.t('academics.history.consentTitle'),
      }),
    ).toBeVisible();
  },
};
export const ConsentRequiredNarrow320 = at(ConsentRequired, 'narrow320');
export const ConsentRequiredPhone390 = at(ConsentRequired, 'phone390');

export const NoLongerAvailable: Story = {
  beforeEach: () =>
    stubFetch((path) =>
      path.includes('/assignments/') ? problemResponse(404, 'not_found') : null,
    ),
  render: () => (
    <ListStory
      path={`/my-homework/${ASSIGNMENT_ID}?student=${CHILD_ID}`}
      session={guardianSession}
      data={[[FAMILY_STUDENTS_KEY, FAMILY_STUDENTS]]}
    />
  ),
  play: async ({ canvasElement }) => {
    await expect(
      await within(canvasElement).findByText(
        i18n.t('assignments.student.itemMissing'),
      ),
    ).toBeVisible();
  },
};
export const NoLongerAvailableNarrow320 = at(NoLongerAvailable, 'narrow320');
export const NoLongerAvailablePhone390 = at(NoLongerAvailable, 'phone390');

export const Empty: Story = {
  render: () => (
    <ListStory
      path="/my-homework"
      session={guardianSession}
      data={[
        [FAMILY_STUDENTS_KEY, FAMILY_STUDENTS],
        [
          studentListKey(CHILD_ID, null),
          pages({
            items: [],
            summary: { pending: 0, missing: 0, submitted: 0, graded: 0 },
            next_cursor: null,
          }),
        ],
      ]}
    />
  ),
  play: async ({ canvasElement }) => {
    await expect(
      within(canvasElement).getByText(i18n.t('assignments.student.empty')),
    ).toBeVisible();
  },
};
export const EmptyNarrow320 = at(Empty, 'narrow320');
export const EmptyPhone390 = at(Empty, 'phone390');

export const Loading: Story = {
  beforeEach: () => stubFetch(() => null),
  render: () => (
    <ListStory
      path="/my-homework"
      session={guardianSession}
      data={[[FAMILY_STUDENTS_KEY, FAMILY_STUDENTS]]}
    />
  ),
  play: async ({ canvasElement }) => {
    await expect(
      canvasElement.querySelector('[aria-busy="true"]'),
    ).not.toBeNull();
  },
};
export const LoadingNarrow320 = at(Loading, 'narrow320');
export const LoadingPhone390 = at(Loading, 'phone390');

export const ErrorState: Story = {
  beforeEach: () =>
    stubFetch((path) =>
      path.endsWith('/assignments')
        ? problemResponse(500, 'internal_error')
        : null,
    ),
  render: () => (
    <ListStory
      path="/my-homework"
      session={guardianSession}
      data={[[FAMILY_STUDENTS_KEY, FAMILY_STUDENTS]]}
    />
  ),
  play: async ({ canvasElement }) => {
    await expect(await within(canvasElement).findByRole('alert')).toBeVisible();
  },
};
export const ErrorStateNarrow320 = at(ErrorState, 'narrow320');
export const ErrorStatePhone390 = at(ErrorState, 'phone390');
