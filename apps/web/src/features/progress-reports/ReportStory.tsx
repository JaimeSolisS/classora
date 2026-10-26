import type { Session } from '@/features/auth/session';
import { StoryAuth } from '@/test/StoryAuth';
import { StoryQueryClient } from '@/test/StoryQueryClient';
import { StoryRouter } from '@/test/StoryRouter';
import type { ProgressReport } from './api';
import { ReportView, type ReportViewProps } from './ReportView';
import { tutorSession } from './storyKit';

// Storybook only (#315 H2): the report page's view in a router, with stub
// actions that never resolve (no request leaves a story).

const never = <T,>() => new Promise<T>(() => {});

export function ReportStory({
  report,
  session = tutorSession,
  ...rest
}: {
  report: ProgressReport;
  session?: Session;
} & Omit<ReportViewProps, 'report' | 'actions'>) {
  return (
    <StoryQueryClient>
      <StoryAuth session={session}>
        <StoryRouter
          routes={[
            {
              path: '/',
              element: (
                <main className="min-w-0 p-4">
                  <ReportView
                    report={report}
                    {...rest}
                    actions={{
                      onSave: never,
                      onSubmit: never,
                      onDelete: never,
                      onApprove: never,
                      onRequestChanges: never,
                      onRetract: never,
                      onRefreshFacts: never,
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
