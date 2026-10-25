import type { Meta, StoryObj } from '@storybook/react-vite';
import { expect, fn, userEvent, within } from 'storybook/test';
import { ApiError } from '@/app/api';
import { i18n } from '@/i18n';
import { toLanguage } from '@/i18n/language';
import { formatInstant } from '@/lib/time';
import {
  APPROVED_RECORD,
  ATTENDANCE_CHANGED,
  CORRECTION_SUBMITTED,
  FINANCE_APPROVED,
  FINANCE_SUBMITTED,
  NO_CLOSEOUT,
  REVIEWABLE,
  STAFF_CHANGES_REQUESTED,
  STAFF_SUBMITTED,
  WAITING,
  ZONE,
} from '@/features/tutor-portal/closeout/fixtures';
import { ApproveCloseoutDialog } from './ApproveCloseoutDialog';
import { CloseoutSection } from './CloseoutSection';
import { CorrectCloseoutDialog } from './CorrectCloseoutDialog';
import { RequestChangesDialog } from './RequestChangesDialog';
import { APPROVED_WITH_OLD_NOTE } from './fixtures';

// The staff session page's Closeout section (#77 H7, #78 I8): submitted (a
// reviewer sees the summary and, with the API's flags, Approve / Request
// changes / Correct), changes requested with the reason, approved with the
// history (and a leftover tutor's note that stays hidden, #312), a
// correction request with the tutor's dated reason, attendance changed
// since approval (Re-approve), Finance (summary and reasons hidden), no
// closeout recorded, waiting for the tutor and loading; the three dialogs
// at 390 px and 1280 px. No request: the decisions are stubs. Axe runs in
// light and dark (the storybook projects).

const VIEWPORTS = {
  phone390: {
    name: 'Phone (390 px)',
    styles: { width: '390px', height: '844px' },
    type: 'mobile',
  },
  desktop1280: {
    name: 'Desktop (1280 px)',
    styles: { width: '1280px', height: '800px' },
    type: 'desktop',
  },
} as const;

const review = {
  onApprove: fn(() => Promise.resolve()),
  onRequestChanges: fn(() => Promise.resolve()),
  onCorrect: fn(() => Promise.resolve()),
  onReload: fn(),
};

const meta = {
  title: 'Scheduling/CloseoutSection',
  component: CloseoutSection,
  args: {
    state: { status: 'ready', closeout: STAFF_SUBMITTED },
    timeZone: ZONE,
  },
  parameters: { viewport: { options: VIEWPORTS } },
  render: (args) => (
    <main className="p-4">
      <CloseoutSection {...args} />
    </main>
  ),
} satisfies Meta<typeof CloseoutSection>;

export default meta;
type Story = StoryObj<typeof meta>;

export const Submitted: Story = {
  play: async ({ canvasElement }) => {
    await expect(
      within(canvasElement).getByText(i18n.t('closeout.submitted')),
    ).toBeInTheDocument();
  },
};

export const Reviewable: Story = {
  args: { state: { status: 'ready', closeout: REVIEWABLE }, review },
  play: async ({ canvasElement }) => {
    const canvas = within(canvasElement);
    for (const name of ['approve', 'requestChanges', 'correct'] as const) {
      await expect(
        canvas.getByRole('button', { name: i18n.t(`closeout.review.${name}`) }),
      ).toBeVisible();
    }
  },
};

export const ChangesRequested: Story = {
  args: {
    state: { status: 'ready', closeout: STAFF_CHANGES_REQUESTED },
    review,
  },
  play: async ({ canvasElement }) => {
    await expect(
      within(canvasElement).getByText(
        i18n.t('closeout.review.changesRequested'),
      ),
    ).toBeInTheDocument();
  },
};

// #312: the fixture keeps a tutor's note from an earlier correction request;
// after the decision the section doesn't show it.
export const Approved: Story = {
  args: {
    state: { status: 'ready', closeout: APPROVED_WITH_OLD_NOTE },
    review,
  },
  play: async ({ canvasElement }) => {
    const canvas = within(canvasElement);
    await expect(
      canvas.getByText(i18n.t('closeout.review.history.heading')),
    ).toBeInTheDocument();
    await expect(
      canvas.queryByText(/Salimos 5 minutos antes/),
    ).not.toBeInTheDocument();
  },
};

export const ApprovedPhone: Story = {
  ...Approved,
  globals: { viewport: { value: 'phone390' } },
};

export const CorrectionSubmitted: Story = {
  args: { state: { status: 'ready', closeout: CORRECTION_SUBMITTED }, review },
  play: async ({ canvasElement }) => {
    // #312: the tutor's reason, dated with the submission under review.
    const label = i18n.t('closeout.review.tutorNote', {
      when: formatInstant('2026-11-12T15:00:00Z', {
        timeZone: ZONE,
        language: toLanguage(i18n.resolvedLanguage),
        style: 'dateTime',
      }),
    });
    await expect(
      within(canvasElement).getByText(`${label}:`),
    ).toBeInTheDocument();
  },
};

export const AttendanceChanged: Story = {
  args: { state: { status: 'ready', closeout: ATTENDANCE_CHANGED }, review },
  play: async ({ canvasElement }) => {
    await userEvent.click(
      within(canvasElement).getByRole('button', {
        name: i18n.t('closeout.review.reapprove'),
      }),
    );
    const dialog = await within(document.body).findByRole('dialog');
    await expect(
      within(dialog).getByText(
        i18n.t('closeout.review.correctDialog.reapproveTitle'),
      ),
    ).toBeInTheDocument();
  },
};

export const SummaryHidden: Story = {
  args: { state: { status: 'ready', closeout: FINANCE_SUBMITTED } },
  play: async ({ canvasElement }) => {
    await expect(
      within(canvasElement).getByText(i18n.t('closeout.summaryHidden')),
    ).toBeInTheDocument();
  },
};

export const FinanceApproved: Story = {
  args: { state: { status: 'ready', closeout: FINANCE_APPROVED } },
  play: async ({ canvasElement }) => {
    await expect(
      within(canvasElement).getByText(
        i18n.t('closeout.review.history.reasonHidden'),
      ),
    ).toBeInTheDocument();
  },
};

export const NoCloseout: Story = {
  args: { state: { status: 'ready', closeout: NO_CLOSEOUT } },
};

export const Waiting: Story = {
  args: { state: { status: 'ready', closeout: WAITING } },
};

export const Loading: Story = {
  args: { state: { status: 'loading' } },
};

// --- The dialogs (390 px and 1280 px) ------------------------------------------

const approveDialog: Story = {
  render: () => (
    <main className="p-4">
      <ApproveCloseoutDialog
        open
        actualMinutes={55}
        scheduledMinutes={60}
        onConfirm={() => Promise.resolve()}
        onCancel={() => undefined}
        onReload={() => undefined}
        initialError={new ApiError({ status: 409, code: 'version_conflict' })}
      />
    </main>
  ),
  play: async () => {
    const dialog = await within(document.body).findByRole('dialog');
    await expect(
      within(dialog).getByRole('button', {
        name: i18n.t('closeout.review.reload'),
      }),
    ).toBeVisible();
  },
};

export const ApproveDialogPhone: Story = {
  ...approveDialog,
  globals: { viewport: { value: 'phone390' } },
};

export const ApproveDialogDesktop: Story = {
  ...approveDialog,
  globals: { viewport: { value: 'desktop1280' } },
};

const requestDialog: Story = {
  render: () => (
    <main className="p-4">
      <RequestChangesDialog
        open
        onConfirm={() => Promise.resolve()}
        onCancel={() => undefined}
        onReload={() => undefined}
        initialReason="Añade la tarea asignada."
      />
    </main>
  ),
  play: async () => {
    const dialog = await within(document.body).findByRole('dialog');
    await expect(
      within(dialog).getByText(i18n.t('closeout.review.requestDialog.title')),
    ).toBeInTheDocument();
  },
};

export const RequestChangesDialogPhone: Story = {
  ...requestDialog,
  globals: { viewport: { value: 'phone390' } },
};

export const RequestChangesDialogDesktop: Story = {
  ...requestDialog,
  globals: { viewport: { value: 'desktop1280' } },
};

const correctDialog: Story = {
  render: () => (
    <main className="p-4">
      <CorrectCloseoutDialog
        open
        record={APPROVED_RECORD}
        scheduledMinutes={60}
        allAbsent={false}
        onConfirm={() => Promise.resolve()}
        onCancel={() => undefined}
        onReload={() => undefined}
      />
    </main>
  ),
  play: async () => {
    const dialog = await within(document.body).findByRole('dialog');
    await expect(
      within(dialog).getByText(
        i18n.t('closeout.review.correctDialog.approvedNote'),
      ),
    ).toBeInTheDocument();
  },
};

export const CorrectDialogPhone: Story = {
  ...correctDialog,
  globals: { viewport: { value: 'phone390' } },
};

export const CorrectDialogDesktop: Story = {
  ...correctDialog,
  globals: { viewport: { value: 'desktop1280' } },
};
