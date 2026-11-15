import type { Session } from '@/features/auth/session';
import { StoryAuth } from '@/test/StoryAuth';
import { StoryQueryClient } from '@/test/StoryQueryClient';
import { StoryRouter } from '@/test/StoryRouter';
import { MyProgressPage } from './MyProgressPage';
import { StudentProgressPage } from './StudentProgressPage';

// Storybook only (#322 G2): the progress pages in a router with a session;
// the story stubs their reads (storyKit's stubPaths).

export function ProgressPageStory({
  path,
  session,
}: {
  path: string;
  session: Session;
}) {
  return (
    <StoryQueryClient>
      <StoryAuth session={session}>
        <StoryRouter
          path={path}
          routes={[
            {
              path: '/my-progress',
              element: (
                <main className="min-w-0 p-4">
                  <MyProgressPage />
                </main>
              ),
            },
            {
              path: '/students/:studentId/progress',
              element: (
                <main className="min-w-0 p-4">
                  <StudentProgressPage />
                </main>
              ),
            },
          ]}
        />
      </StoryAuth>
    </StoryQueryClient>
  );
}
