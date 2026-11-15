import type { Meta, StoryObj } from '@storybook/react-vite';
import { expect, fn, screen, within } from 'storybook/test';
import { i18n } from '@/i18n';
import { StoryRouter } from '@/test/StoryRouter';
import { CLASSROOMS, SOFT_CONFLICTS } from '../create/fixtures';
import type { ChangeRequestReview } from './api';
import { ChangeRequestDecisionPanel } from './ChangeRequestDecisionPanel';
import {
  ChangeRequestReviewView,
  type ChangeRequestReviewViewProps,
} from './ChangeRequestReviewView';
import {
  APPROVED_EXCEPTION,
  APPROVED_RESULT,
  PENDING_CANCELLATION,
  PENDING_RESCHEDULE,
  REJECTED,
} from './fixtures';

// "Review change request" (#62 G4): a pending cancellation, a pending
// reschedule with a conflict (and the override action), the exception
// dialog with a validation error, approved as an exception, rejected, and
// the 320 px layout. No request: the panel's calls are stubs. Axe runs in
// light and dark.

const ZONE = 'America/Mexico_City';

function ReviewStory(props: ChangeRequestReviewViewProps) {
  return (
    <StoryRouter
      routes={[
        {
          path: '/',
          element: (
            <main className="p-4">
              <h1 className="mb-4 text-2xl font-semibold">
                {i18n.t('scheduling.changeRequest.title')}
              </h1>
              <ChangeRequestReviewView {...props} />
            </main>
          ),
        },
      ]}
    />
  );
}

const panel = (
  review: ChangeRequestReview,
  extra: Partial<Parameters<typeof ChangeRequestDecisionPanel>[0]> = {},
) => (
  <ChangeRequestDecisionPanel
    review={review}
    classrooms={CLASSROOMS}
    canOverride
    canApprove={review.can_decide}
    onApprove={fn(() => Promise.resolve(APPROVED_RESULT))}
    onApproved={fn()}
    onReject={fn(() => Promise.resolve())}
    {...extra}
  />
);

const link = (sessionId: string) => (
  <a href={`/schedule/sessions/${sessionId}`} className="underline">
    {i18n.t('scheduling.changeRequest.openSession')}
  </a>
);

const meta = {
  title: 'Scheduling/ChangeRequestReviewView',
  component: ReviewStory,
  args: {
    state: { status: 'ready', review: PENDING_CANCELLATION },
    timeZone: ZONE,
    decisionPanel: panel(PENDING_CANCELLATION),
    sessionLink: link,
  },
  parameters: {
    viewport: {
      options: {
        narrow320: {
          name: 'Narrow (320 px)',
          styles: { width: '320px', height: '640px' },
          type: 'mobile',
        },
      },
    },
  },
} satisfies Meta<typeof ReviewStory>;

export default meta;
type Story = StoryObj<typeof meta>;

export const PendingCancellation: Story = {
  play: async ({ canvasElement }) => {
    const canvas = within(canvasElement);
    await expect(
      canvas.getByText(
        i18n.t('scheduling.changeRequest.outcomeText', {
          classification: i18n.t(
            'scheduling.changeRequest.classification.late',
          ),
          makeup: i18n.t('scheduling.changeRequest.noMakeup'),
        }),
      ),
    ).toBeInTheDocument();
    await expect(
      canvas.getByRole('button', {
        name: i18n.t('scheduling.changeRequest.decide.exception'),
      }),
    ).toBeInTheDocument();
  },
};

export const RescheduleWithConflict: Story = {
  args: {
    state: { status: 'ready', review: PENDING_RESCHEDULE },
    decisionPanel: panel(PENDING_RESCHEDULE, {
      initialConflicts: SOFT_CONFLICTS,
    }),
  },
  play: async ({ canvasElement }) => {
    await expect(
      within(canvasElement).getByRole('button', {
        name: i18n.t('scheduling.changeRequest.decide.overrideAction'),
      }),
    ).toBeInTheDocument();
  },
};

export const ExceptionDialogWithError: Story = {
  args: {
    decisionPanel: panel(PENDING_CANCELLATION, {
      initialDialog: 'exception',
      initialProblem: 'required',
    }),
  },
  play: async () => {
    const dialog = await screen.findByRole('dialog');
    await expect(
      within(dialog).getByText(
        i18n.t('scheduling.changeRequest.reasonRequired'),
      ),
    ).toBeInTheDocument();
    await expect(
      within(dialog).getByText(
        i18n.t('scheduling.changeRequest.exception.description'),
      ),
    ).toBeInTheDocument();
  },
};

export const ApprovedAsException: Story = {
  args: {
    state: { status: 'ready', review: APPROVED_EXCEPTION },
    approved: { warnings: [] },
  },
  play: async ({ canvasElement }) => {
    await expect(
      within(canvasElement).getByText(
        i18n.t('scheduling.changeRequest.approvedExceptionText'),
      ),
    ).toBeInTheDocument();
  },
};

export const Rejected: Story = {
  args: { state: { status: 'ready', review: REJECTED } },
  play: async ({ canvasElement }) => {
    await expect(
      within(canvasElement).getByText(
        i18n.t('scheduling.changeRequest.reasonText', {
          reason: REJECTED.decision?.reason,
        }),
      ),
    ).toBeInTheDocument();
  },
};

export const Narrow: Story = {
  args: {
    state: { status: 'ready', review: PENDING_RESCHEDULE },
    decisionPanel: panel(PENDING_RESCHEDULE),
  },
  globals: { viewport: { value: 'narrow320' } },
};
