import type { Meta, StoryObj } from '@storybook/react-vite';
import { expect, within } from 'storybook/test';
import { ApiError } from '@/app/api';
import { studentDetailKey } from '@/features/students/api';
import { JOSE_LIMITED } from '@/features/students/fixtures';
import { i18n } from '@/i18n';
import { StoryAuth } from '@/test/StoryAuth';
import { StoryQueryClient } from '@/test/StoryQueryClient';
import { StoryRouter } from '@/test/StoryRouter';
import { createHintsKey, type ApprovedReportPage } from './api';
import {
  APPROVED_PAGE,
  APPROVED_PAGE_NO_CREATE,
  REPORT_ID,
  STUDENT_ID,
} from './fixtures';
import { StartReportForm, type StartReportFormProps } from './StartReportForm';
import { StartReportPage } from './StartReportPage';
import { noSideScroll, tutorSession, VIEWPORTS } from './storyKit';

// The start step (#315 H2): weekly, monthly and on demand (the defaults),
// field errors, a period that already has a report, and can_create false.
// Every story has 320 and 390 px variants that assert the page never
// scrolls sideways. Queries are seeded and the create never resolves: no
// request leaves the story. Axe runs in light and dark.

const TODAY = '2026-11-04';

function StartStory({
  hints = APPROVED_PAGE,
  form,
}: {
  hints?: ApprovedReportPage;
  /** Render the form alone with these props (errors, failures). */
  form?: Partial<StartReportFormProps>;
}) {
  return (
    <StoryQueryClient
      data={[
        [studentDetailKey(STUDENT_ID), JOSE_LIMITED],
        [createHintsKey(STUDENT_ID), hints],
      ]}
    >
      <StoryAuth session={tutorSession}>
        <StoryRouter
          path={`/students/${STUDENT_ID}/progress-reports/new`}
          routes={[
            {
              path: '/students/:studentId/progress-reports/new',
              element: (
                <main className="min-w-0 p-4">
                  {form ? (
                    <div className="flex flex-col gap-6">
                      <h1 className="text-2xl font-semibold">
                        {i18n.t('progressReports.start.title')}
                      </h1>
                      <StartReportForm
                        studentId={STUDENT_ID}
                        today={TODAY}
                        subjects={hints.creatable_subjects.map((subject) => ({
                          id: subject.id,
                          label: subject.name,
                        }))}
                        onStart={() => new Promise<void>(() => {})}
                        {...form}
                      />
                    </div>
                  ) : (
                    <StartReportPage />
                  )}
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
  title: 'Progress reports/Start report',
  component: StartStory,
  parameters: { viewport: { options: VIEWPORTS } },
} satisfies Meta<typeof StartStory>;

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

export const OnDemandDefaults: Story = {
  play: async ({ canvasElement }) => {
    await expect(
      await within(canvasElement).findByLabelText(
        i18n.t('progressReports.start.from'),
      ),
    ).toBeVisible();
  },
};
export const OnDemandDefaultsNarrow320 = at(OnDemandDefaults, 'narrow320');
export const OnDemandDefaultsPhone390 = at(OnDemandDefaults, 'phone390');

export const Weekly: Story = {
  args: { form: { initialValues: { kind: 'weekly', weekDate: TODAY } } },
  play: async ({ canvasElement }) => {
    await expect(
      within(canvasElement).getByRole('button', {
        name: i18n.t('progressReports.start.lastWeek'),
      }),
    ).toBeVisible();
  },
};
export const WeeklyNarrow320 = at(Weekly, 'narrow320');
export const WeeklyPhone390 = at(Weekly, 'phone390');

export const Monthly: Story = {
  args: { form: { initialValues: { kind: 'monthly' } } },
  play: async ({ canvasElement }) => {
    await expect(
      within(canvasElement).getByLabelText(
        i18n.t('progressReports.start.month'),
      ),
    ).toBeVisible();
  },
};
export const MonthlyNarrow320 = at(Monthly, 'narrow320');
export const MonthlyPhone390 = at(Monthly, 'phone390');

export const FieldErrors: Story = {
  args: {
    form: {
      initialValues: { from: '2025-01-01', to: '2026-01-02' },
      initialErrors: {
        to: 'progressReports.start.errors.tooLong',
        subject: 'errors:report_subject_not_assigned',
      },
    },
  },
  play: async ({ canvasElement }) => {
    await expect(
      within(canvasElement).getByText(
        i18n.t('progressReports.start.errors.tooLong'),
      ),
    ).toBeVisible();
  },
};
export const FieldErrorsNarrow320 = at(FieldErrors, 'narrow320');
export const FieldErrorsPhone390 = at(FieldErrors, 'phone390');

export const AlreadyExists: Story = {
  args: {
    form: {
      initialValues: { kind: 'monthly' },
      initialFailure: {
        kind: 'exists',
        reportId: REPORT_ID,
        error: new ApiError({
          code: 'progress_report_exists',
          status: 409,
          requestId: 'story-exists',
        }),
      },
    },
  },
  play: async ({ canvasElement }) => {
    await expect(
      within(canvasElement).getByRole('link', {
        name: i18n.t('progressReports.start.openExisting'),
      }),
    ).toBeVisible();
  },
};
export const AlreadyExistsNarrow320 = at(AlreadyExists, 'narrow320');
export const AlreadyExistsPhone390 = at(AlreadyExists, 'phone390');

export const CannotCreate: Story = {
  args: { hints: APPROVED_PAGE_NO_CREATE },
  play: async ({ canvasElement }) => {
    await expect(
      await within(canvasElement).findByText(
        i18n.t('progressReports.start.cannotCreate'),
      ),
    ).toBeVisible();
  },
};
export const CannotCreateNarrow320 = at(CannotCreate, 'narrow320');
export const CannotCreatePhone390 = at(CannotCreate, 'phone390');
