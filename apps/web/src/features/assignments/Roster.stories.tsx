import type { Meta, StoryObj } from '@storybook/react-vite';
import type { QueryKey } from '@tanstack/react-query';
import { expect, within } from 'storybook/test';
import type { Session } from '@/features/auth/session';
import { SUBJECT_OPTIONS_KEY } from '@/features/courses/api';
import { i18n } from '@/i18n';
import { StoryAuth } from '@/test/StoryAuth';
import { StoryQueryClient } from '@/test/StoryQueryClient';
import { StoryRouter } from '@/test/StoryRouter';
import { workingListKey, type AssignmentView } from './api';
import { AssignmentsPage } from './AssignmentsPage';
import {
  ANA_ID,
  ASSIGNMENT,
  FEEDBACK_ONLY,
  FRESH,
  LUIS_ID,
  READ_ONLY,
  WORKING_LIST,
} from './fixtures';
import { RosterView, type RosterDialog } from './RosterView';
import { noSideScroll, tutorSession, VIEWPORTS } from './storyKit';

// The working list and the roster (#313 G2): the list (own and someone
// else's rows, empty, empty with filters), a roster with every state
// (pending, missing, handed in late with a link, recorded by the tutor,
// graded and updated with the internal reason), feedback-only, read-only
// (a holder or a former creator), and the grade (first, re-grade), record
// and delete dialogs open. Every story has 320 and 390 px variants that
// assert the page never scrolls sideways, plus 1280 px for the list and the
// roster. Queries are seeded and the writes are stubs: no request leaves
// the story. Axe runs in light and dark.

const SUBJECTS = [
  { id: ASSIGNMENT.subject.id, name: 'Matemáticas', levelName: 'Secundaria' },
];

function ListStory({
  path = '/assignments',
  data,
  session = tutorSession,
}: {
  path?: string;
  data: ReadonlyArray<readonly [QueryKey, unknown]>;
  session?: Session;
}) {
  return (
    <StoryQueryClient data={[[SUBJECT_OPTIONS_KEY, SUBJECTS], ...data]}>
      <StoryAuth session={session}>
        <StoryRouter
          path={path}
          routes={[
            {
              path: '/assignments',
              element: (
                <main className="min-w-0 p-4">
                  <AssignmentsPage />
                </main>
              ),
            },
          ]}
        />
      </StoryAuth>
    </StoryQueryClient>
  );
}

function RosterStory({
  assignment,
  dialog = null,
}: {
  assignment: AssignmentView;
  dialog?: RosterDialog | null;
}) {
  return (
    <StoryQueryClient>
      <StoryAuth session={tutorSession}>
        <StoryRouter
          routes={[
            {
              path: '/',
              element: (
                <main className="min-w-0 p-4">
                  <RosterView
                    assignment={assignment}
                    initialDialog={dialog}
                    actions={{
                      onGrade: () => new Promise<void>(() => {}),
                      onRecord: () => new Promise<void>(() => {}),
                      onDelete: () => new Promise<void>(() => {}),
                      onReload: () => undefined,
                    }}
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
  title: 'Assignments/Roster',
  component: RosterStory,
  args: { assignment: ASSIGNMENT },
  parameters: { viewport: { options: VIEWPORTS } },
} satisfies Meta<typeof RosterStory>;

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

const pages = (list: typeof WORKING_LIST) => ({
  pages: [list],
  pageParams: [null],
});

export const List: Story = {
  render: () => (
    <ListStory
      data={[[workingListKey('upcoming', null), pages(WORKING_LIST)]]}
    />
  ),
  play: async ({ canvasElement }) => {
    const canvas = within(canvasElement);
    await expect(canvas.getAllByRole('listitem')).toHaveLength(2);
    await expect(
      canvas.getByRole('link', { name: i18n.t('assignments.setHomework') }),
    ).toBeVisible();
  },
};
export const ListNarrow320 = at(List, 'narrow320');
export const ListPhone390 = at(List, 'phone390');
export const ListDesktop1280 = at(List, 'desktop1280');

export const ListEmpty: Story = {
  render: () => (
    <ListStory
      data={[
        [
          workingListKey('upcoming', null),
          pages({ items: [], next_cursor: null }),
        ],
      ]}
    />
  ),
  play: async ({ canvasElement }) => {
    await expect(
      within(canvasElement).getByText(i18n.t('assignments.empty')),
    ).toBeVisible();
  },
};
export const ListEmptyNarrow320 = at(ListEmpty, 'narrow320');
export const ListEmptyPhone390 = at(ListEmpty, 'phone390');

export const ListFilteredEmpty: Story = {
  render: () => (
    <ListStory
      path="/assignments?due=past"
      data={[
        [workingListKey('past', null), pages({ items: [], next_cursor: null })],
      ]}
    />
  ),
  play: async ({ canvasElement }) => {
    await expect(
      within(canvasElement).getByRole('button', {
        name: i18n.t('assignments.clearFilters'),
      }),
    ).toBeVisible();
  },
};
export const ListFilteredEmptyNarrow320 = at(ListFilteredEmpty, 'narrow320');
export const ListFilteredEmptyPhone390 = at(ListFilteredEmpty, 'phone390');

export const MixedStates: Story = {
  play: async ({ canvasElement }) => {
    const canvas = within(canvasElement);
    await expect(
      canvas.getByText(i18n.t('assignments.recordedByTutor'), { exact: false }),
    ).toBeVisible();
    await expect(
      canvas.getByRole('link', { name: /fracciones/ }),
    ).toHaveAttribute('rel', 'noopener noreferrer nofollow');
  },
};
export const MixedStatesNarrow320 = at(MixedStates, 'narrow320');
export const MixedStatesPhone390 = at(MixedStates, 'phone390');
export const MixedStatesDesktop1280 = at(MixedStates, 'desktop1280');

export const FeedbackOnly: Story = {
  args: { assignment: FEEDBACK_ONLY },
  play: async ({ canvasElement }) => {
    await expect(
      within(canvasElement).getByText(i18n.t('assignments.feedbackOnly')),
    ).toBeVisible();
  },
};
export const FeedbackOnlyNarrow320 = at(FeedbackOnly, 'narrow320');
export const FeedbackOnlyPhone390 = at(FeedbackOnly, 'phone390');

export const ReadOnly: Story = {
  args: { assignment: READ_ONLY },
  play: async ({ canvasElement }) => {
    await expect(within(canvasElement).queryAllByRole('button')).toHaveLength(
      0,
    );
  },
};
export const ReadOnlyNarrow320 = at(ReadOnly, 'narrow320');
export const ReadOnlyPhone390 = at(ReadOnly, 'phone390');

const dialogOpen = async (canvasElement: HTMLElement) => {
  await expect(
    await within(canvasElement.ownerDocument.body).findByRole('dialog'),
  ).toBeVisible();
};

export const GradeDialogFirst: Story = {
  args: { dialog: { kind: 'grade', studentId: ANA_ID } },
  play: async ({ canvasElement }) => dialogOpen(canvasElement),
};
export const GradeDialogFirstNarrow320 = at(GradeDialogFirst, 'narrow320');
export const GradeDialogFirstPhone390 = at(GradeDialogFirst, 'phone390');

export const GradeDialogRegrade: Story = {
  args: { dialog: { kind: 'grade', studentId: LUIS_ID } },
  play: async ({ canvasElement }) => {
    await dialogOpen(canvasElement);
    await expect(
      within(canvasElement.ownerDocument.body).getByLabelText(
        i18n.t('assignments.grade.reason'),
      ),
    ).toBeVisible();
  },
};
export const GradeDialogRegradeNarrow320 = at(GradeDialogRegrade, 'narrow320');
export const GradeDialogRegradePhone390 = at(GradeDialogRegrade, 'phone390');

export const RecordDialog: Story = {
  args: {
    dialog: { kind: 'record', studentId: ASSIGNMENT.roster[1]!.student.id },
  },
  play: async ({ canvasElement }) => dialogOpen(canvasElement),
};
export const RecordDialogNarrow320 = at(RecordDialog, 'narrow320');
export const RecordDialogPhone390 = at(RecordDialog, 'phone390');

export const DeleteDialog: Story = {
  args: { assignment: FRESH, dialog: { kind: 'delete' } },
  play: async ({ canvasElement }) => dialogOpen(canvasElement),
};
export const DeleteDialogNarrow320 = at(DeleteDialog, 'narrow320');
export const DeleteDialogPhone390 = at(DeleteDialog, 'phone390');
