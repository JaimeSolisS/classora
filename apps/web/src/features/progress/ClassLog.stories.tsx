import type { Meta, StoryObj } from '@storybook/react-vite';
import { expect, userEvent, within } from 'storybook/test';
import { i18n } from '@/i18n';
import { StoryAuth } from '@/test/StoryAuth';
import { StoryQueryClient } from '@/test/StoryQueryClient';
import { StoryRouter } from '@/test/StoryRouter';
import { ClassLog, type LogAudience, type LogState } from './ClassLog';
import { LOG_ITEMS, NOTES_PAGE, STUDENT } from './fixtures';
import {
  coordinatorSession,
  guardianSession,
  json,
  noSideScroll,
  stubPaths,
  VIEWPORTS,
} from './storyKit';

// The class log (#322 G2): approved summaries, a null summary, every
// attendance badge, a <script> as text, the family's notes disclosure
// (opened by the play function; the notes are stubbed), the staff note
// count with "Session details", and "Load more", at 320, 390 and 1280 px.
// Axe runs in light and dark.

const state = (hasMore = false): LogState => ({
  status: 'ready',
  items: LOG_ITEMS,
  hasMore,
  loadingMore: false,
  onRetry: () => undefined,
  onMore: () => new Promise(() => {}),
});

function Log({
  audience,
  hasMore = false,
}: {
  audience: LogAudience;
  hasMore?: boolean;
}) {
  return (
    <StoryQueryClient>
      <StoryAuth
        session={audience === 'family' ? guardianSession : coordinatorSession}
      >
        <StoryRouter
          routes={[
            {
              path: '/',
              element: (
                <main className="min-w-0 p-4">
                  <ClassLog
                    state={state(hasMore)}
                    audience={audience}
                    studentId={STUDENT}
                    showsSessionLink={audience === 'staff'}
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
  title: 'Progress/Class log',
  component: Log,
  args: { audience: 'family' },
  parameters: { viewport: { options: VIEWPORTS } },
} satisfies Meta<typeof Log>;

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

export const FamilyNotesOpen: Story = {
  beforeEach: () => stubPaths({ '/learning-notes': json(NOTES_PAGE) }),
  play: async ({ canvasElement }) => {
    const canvas = within(canvasElement);
    for (const key of [
      'attendance.statuses.present',
      'attendance.statuses.late',
      'attendance.statuses.absent',
      'attendance.notRecorded',
    ] as const) {
      await expect(canvas.getAllByText(i18n.t(key))[0]).toBeVisible();
    }
    await expect(
      canvas.getAllByText(i18n.t('progress.log.noSummary'))[0],
    ).toBeVisible();
    const toggle = canvas.getByRole('button', {
      name: i18n.t('progress.log.notes', { count: 2 }),
    });
    await userEvent.click(toggle);
    await expect(toggle).toHaveAttribute('aria-expanded', 'true');
    await expect(
      await canvas.findByText(/Muy buena participación/),
    ).toBeVisible();
  },
};
export const FamilyNotesOpenNarrow320 = at(FamilyNotesOpen, 'narrow320');
export const FamilyNotesOpenPhone390 = at(FamilyNotesOpen, 'phone390');
export const FamilyNotesOpenDesktop1280 = at(FamilyNotesOpen, 'desktop1280');

export const StaffCount: Story = {
  args: { audience: 'staff' },
  play: async ({ canvasElement }) => {
    const canvas = within(canvasElement);
    await expect(
      canvas.getByText(i18n.t('progress.log.noteCount', { count: 2 })),
    ).toBeVisible();
    await expect(
      canvas.getAllByRole('link', { name: i18n.t('progress.log.details') }),
    ).toHaveLength(LOG_ITEMS.length);
  },
};
export const StaffCountNarrow320 = at(StaffCount, 'narrow320');
export const StaffCountPhone390 = at(StaffCount, 'phone390');
export const StaffCountDesktop1280 = at(StaffCount, 'desktop1280');

export const LoadMore: Story = {
  args: { audience: 'family', hasMore: true },
  play: async ({ canvasElement }) => {
    await expect(
      within(canvasElement).getByRole('button', {
        name: i18n.t('progress.log.loadMore'),
      }),
    ).toBeVisible();
  },
};
export const LoadMoreNarrow320 = at(LoadMore, 'narrow320');
export const LoadMorePhone390 = at(LoadMore, 'phone390');
export const LoadMoreDesktop1280 = at(LoadMore, 'desktop1280');
