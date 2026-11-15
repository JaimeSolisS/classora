import type { Meta, StoryObj } from '@storybook/react-vite';
import { expect, fn, screen, userEvent, within } from 'storybook/test';
import { i18n } from '@/i18n';
import { StoryRouter } from '@/test/StoryRouter';
import {
  CLASSROOMS,
  CREATED,
  SOFT_CONFLICTS,
  TUTOR_SUGGESTIONS,
  TUTORS,
} from '../create/fixtures';
import { OfferPanel } from '../session/OfferPanel';
import type { SessionRequestConfirmed } from './api';
import {
  CONFIRMED,
  CONFIRMED_REVIEW,
  PENDING_REVIEW,
  REJECTED_REVIEW,
} from './fixtures';
import { RejectRequestDialog } from './RejectRequestDialog';
import {
  RequestReviewView,
  type RequestReviewViewProps,
} from './RequestReviewView';

// "Review session request" (#60 H3): pending with suggested tutors, a
// conflict with the override action, the reject dialog with a validation
// error, confirmed (with the session link) and rejected (with the reason),
// and the 320 px layout. No request: the panel's calls are stubs. Axe runs
// in light and dark.

const ZONE = 'America/Mexico_City';

function ReviewStory(props: RequestReviewViewProps) {
  return (
    <StoryRouter
      routes={[
        {
          path: '/',
          element: (
            <main className="p-4">
              <h1 className="mb-4 text-2xl font-semibold">
                {i18n.t('scheduling.requestReview.title')}
              </h1>
              <RequestReviewView {...props} />
            </main>
          ),
        },
      ]}
    />
  );
}

const panel = (conflicts?: typeof SOFT_CONFLICTS) => (
  <OfferPanel<SessionRequestConfirmed>
    variant="confirm"
    tutors={TUTORS}
    classrooms={CLASSROOMS}
    timeZone={ZONE}
    canOverride
    onSuggest={fn(() => Promise.resolve(TUTOR_SUGGESTIONS))}
    onSubmit={fn(() => Promise.resolve(CONFIRMED))}
    onOffered={fn()}
    initialConflicts={conflicts}
  />
);

const link = (sessionId: string) => (
  <a href={`/schedule/sessions/${sessionId}`} className="underline">
    {i18n.t('scheduling.requestReview.openSession')}
  </a>
);

const meta = {
  title: 'Scheduling/RequestReviewView',
  component: ReviewStory,
  args: {
    state: { status: 'ready', review: PENDING_REVIEW },
    timeZone: ZONE,
    confirmPanel: panel(),
    onReject: fn(),
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

export const PendingWithSuggestions: Story = {
  play: async ({ canvasElement }) => {
    const canvas = within(canvasElement);
    await userEvent.click(
      canvas.getByRole('button', {
        name: i18n.t('scheduling.tutorSuggestions.button'),
      }),
    );
    await expect(
      await canvas.findByRole('button', {
        name: i18n.t('scheduling.tutorSuggestions.chooseTutor', {
          name: 'Diego Torres',
        }),
      }),
    ).toBeInTheDocument();
    await expect(
      canvas.getByRole('button', {
        name: i18n.t('scheduling.requestReview.reject.open'),
      }),
    ).toBeInTheDocument();
  },
};

export const ConflictWithOverride: Story = {
  args: { confirmPanel: panel(SOFT_CONFLICTS) },
  play: async ({ canvasElement }) => {
    await expect(
      within(canvasElement).getByRole('button', {
        name: i18n.t('scheduling.requestReview.confirm.overrideAction'),
      }),
    ).toBeInTheDocument();
  },
};

export const RejectDialogWithError: Story = {
  render: (args) => (
    <>
      <ReviewStory {...args} />
      <RejectRequestDialog
        open
        onConfirm={fn(() => Promise.resolve())}
        onCancel={fn()}
        initialProblem="required"
      />
    </>
  ),
  play: async () => {
    const dialog = await screen.findByRole('dialog');
    await expect(
      within(dialog).getByText(
        i18n.t('scheduling.requestReview.reject.reasonRequired'),
      ),
    ).toBeInTheDocument();
    await expect(
      within(dialog).getByText(
        i18n.t('scheduling.requestReview.reject.description'),
      ),
    ).toBeInTheDocument();
  },
};

export const Confirmed: Story = {
  args: {
    state: { status: 'ready', review: CONFIRMED_REVIEW },
    confirmed: {
      sessionId: CREATED.id,
      tutorName: 'Carmen Ruiz',
      warnings: [],
    },
  },
  play: async ({ canvasElement }) => {
    await expect(
      within(canvasElement).getByRole('link', {
        name: i18n.t('scheduling.requestReview.openSession'),
      }),
    ).toBeInTheDocument();
  },
};

export const Rejected: Story = {
  args: { state: { status: 'ready', review: REJECTED_REVIEW } },
  play: async ({ canvasElement }) => {
    await expect(
      within(canvasElement).getByText(
        i18n.t('scheduling.requestReview.reasonText', {
          reason: REJECTED_REVIEW.rejection_reason,
        }),
      ),
    ).toBeInTheDocument();
  },
};

export const Narrow: Story = {
  globals: { viewport: { value: 'narrow320' } },
};
