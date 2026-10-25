import type { Meta, StoryObj } from '@storybook/react-vite';
import { expect, fn, within } from 'storybook/test';
import { ApiError } from '@/app/api';
import { i18n } from '@/i18n';
import { CloseoutFlow } from './CloseoutFlow';
import {
  ATTENDANCE_COMPLETED,
  ATTENDANCE_LOCKED,
  ATTENDANCE_MARKED,
  ATTENDANCE_UNMARKED,
  NOT_STARTED,
  OPEN,
  SUBMITTED,
  SUMMARY,
  TUTOR_APPROVED,
  TUTOR_CHANGES_REQUESTED,
  TUTOR_CHANGES_REQUESTED_ON_CORRECTION,
  TUTOR_CORRECTION_SUBMITTED,
  TUTOR_RESUBMITTED,
  ZONE,
} from './fixtures';

// The tutor's closeout flow (#77 H7): each step at 390 px and 320 px, the
// review, the read-only view and the not-started block. #305: the review
// status views (under review, correction under review, changes requested
// with a long reason, with and without an approval in force, approved) and
// the edit modes (resubmit step 1 editable and step 4 with the note;
// correction step 1 read-only and step 4 with the reason error; the
// "nothing changed" alert). No request: the callbacks are stubs. Axe runs
// in light and dark (the storybook projects).

const VIEWPORTS = {
  narrow320: {
    name: 'Narrow (320 px)',
    styles: { width: '320px', height: '640px' },
    type: 'mobile',
  },
  phone390: {
    name: 'Phone (390 px)',
    styles: { width: '390px', height: '844px' },
    type: 'mobile',
  },
} as const;

const meta = {
  title: 'Tutor portal/CloseoutFlow',
  component: CloseoutFlow,
  args: {
    state: { status: 'ready', closeout: OPEN },
    attendance: { status: 'ready', attendance: ATTENDANCE_MARKED },
    timeZone: ZONE,
    onSaveAttendance: fn(() => Promise.resolve()),
    onReload: fn(),
    onSubmit: fn(() => Promise.resolve()),
    onResubmit: fn(() => Promise.resolve()),
    onEditModeChange: fn(),
  },
  parameters: { viewport: { options: VIEWPORTS } },
  globals: { viewport: { value: 'phone390' } },
  render: (args) => (
    <main className="p-4">
      <h1 className="mb-4 text-2xl font-semibold">
        {i18n.t('closeout.title')}
      </h1>
      <CloseoutFlow {...args} />
    </main>
  ),
} satisfies Meta<typeof CloseoutFlow>;

export default meta;
type Story = StoryObj<typeof meta>;

async function fitsTheWidth(canvasElement: HTMLElement) {
  const main = canvasElement.querySelector('main')!;
  await expect(main.scrollWidth).toBeLessThanOrEqual(main.clientWidth);
}

async function onStep(canvasElement: HTMLElement, step: number) {
  const current = canvasElement.querySelector('[aria-current="step"]');
  await expect(current?.textContent).toContain(`${step}.`);
  await fitsTheWidth(canvasElement);
}

export const AttendanceStep: Story = {
  play: async ({ canvasElement }) => onStep(canvasElement, 1),
};

export const AttendanceMissing: Story = {
  args: { attendance: { status: 'ready', attendance: ATTENDANCE_UNMARKED } },
  play: async ({ canvasElement }) => {
    const next = within(canvasElement).getByRole('button', {
      name: i18n.t('closeout.next'),
    });
    await expect(next).toBeDisabled();
    await onStep(canvasElement, 1);
  },
};

export const AttendanceStepNarrow320: Story = {
  globals: { viewport: { value: 'narrow320' } },
  args: { attendance: { status: 'ready', attendance: ATTENDANCE_UNMARKED } },
  play: async ({ canvasElement }) => onStep(canvasElement, 1),
};

export const TimeStep: Story = {
  args: { initialStep: 2 },
  play: async ({ canvasElement }) => onStep(canvasElement, 2),
};

export const TimeStepNarrow320: Story = {
  globals: { viewport: { value: 'narrow320' } },
  args: { initialStep: 2 },
  play: async ({ canvasElement }) => onStep(canvasElement, 2),
};

export const SummaryStep: Story = {
  args: { initialStep: 3 },
  play: async ({ canvasElement }) => onStep(canvasElement, 3),
};

export const SummaryStepNarrow320: Story = {
  globals: { viewport: { value: 'narrow320' } },
  args: { initialStep: 3, initialSummary: SUMMARY },
  play: async ({ canvasElement }) => onStep(canvasElement, 3),
};

export const ReviewStep: Story = {
  args: { initialStep: 4, initialSummary: SUMMARY },
  play: async ({ canvasElement, args }) => {
    await onStep(canvasElement, 4);
    within(canvasElement)
      .getByRole('button', { name: i18n.t('closeout.submit') })
      .click();
    await expect(args.onSubmit).toHaveBeenCalledWith({
      actual_minutes: 60,
      summary: SUMMARY,
    });
  },
};

export const ReviewStepNarrow320: Story = {
  globals: { viewport: { value: 'narrow320' } },
  args: { initialStep: 4, initialSummary: SUMMARY },
  play: async ({ canvasElement }) => onStep(canvasElement, 4),
};

export const ReadOnly: Story = {
  args: { state: { status: 'ready', closeout: SUBMITTED } },
  play: async ({ canvasElement }) => {
    await expect(
      within(canvasElement).getByText(i18n.t('closeout.submitted')),
    ).toBeInTheDocument();
    await fitsTheWidth(canvasElement);
  },
};

export const ReadOnlyNarrow320: Story = {
  globals: { viewport: { value: 'narrow320' } },
  args: { state: { status: 'ready', closeout: SUBMITTED } },
  play: async ({ canvasElement }) => fitsTheWidth(canvasElement),
};

export const NotStarted: Story = {
  args: { state: { status: 'ready', closeout: NOT_STARTED } },
  play: async ({ canvasElement }) => {
    await expect(canvasElement.querySelector('ol')).toBeNull();
    await fitsTheWidth(canvasElement);
  },
};

export const Loading: Story = {
  args: { state: { status: 'loading' } },
};

// --- #305 ------------------------------------------------------------------

async function showsHeading(canvasElement: HTMLElement, name: string) {
  await expect(
    within(canvasElement).getByRole('heading', { level: 3, name }),
  ).toBeVisible();
  await fitsTheWidth(canvasElement);
}

async function targetsAreBigEnough(canvasElement: HTMLElement) {
  for (const button of within(canvasElement).getAllByRole('button')) {
    const box = button.getBoundingClientRect();
    await expect(box.width).toBeGreaterThanOrEqual(24);
    await expect(box.height).toBeGreaterThanOrEqual(24);
  }
}

export const UnderReview: Story = {
  args: { state: { status: 'ready', closeout: TUTOR_RESUBMITTED } },
  play: async ({ canvasElement }) =>
    showsHeading(canvasElement, i18n.t('closeout.tutorView.underReview')),
};

export const CorrectionUnderReview: Story = {
  args: { state: { status: 'ready', closeout: TUTOR_CORRECTION_SUBMITTED } },
  play: async ({ canvasElement }) =>
    showsHeading(
      canvasElement,
      i18n.t('closeout.tutorView.correctionUnderReview'),
    ),
};

export const CorrectionUnderReviewNarrow320: Story = {
  ...CorrectionUnderReview,
  globals: { viewport: { value: 'narrow320' } },
};

export const ChangesRequested: Story = {
  args: { state: { status: 'ready', closeout: TUTOR_CHANGES_REQUESTED } },
  play: async ({ canvasElement, args }) => {
    await showsHeading(
      canvasElement,
      i18n.t('closeout.tutorView.changesRequested'),
    );
    await targetsAreBigEnough(canvasElement);
    within(canvasElement)
      .getByRole('button', { name: i18n.t('closeout.tutorView.resubmit') })
      .click();
    await expect(args.onEditModeChange).toHaveBeenCalledWith('resubmit');
  },
};

export const ChangesRequestedNarrow320: Story = {
  globals: { viewport: { value: 'narrow320' } },
  args: { state: { status: 'ready', closeout: TUTOR_CHANGES_REQUESTED } },
  play: async ({ canvasElement }) =>
    showsHeading(canvasElement, i18n.t('closeout.tutorView.changesRequested')),
};

/** A long reason (about 1000 characters) and the approval in force. */
export const ChangesRequestedOnCorrection: Story = {
  args: {
    state: { status: 'ready', closeout: TUTOR_CHANGES_REQUESTED_ON_CORRECTION },
  },
  play: async ({ canvasElement }) => {
    await showsHeading(
      canvasElement,
      i18n.t('closeout.tutorView.changesRequested'),
    );
    await expect(
      within(canvasElement).getByText(
        i18n.t('closeout.tutorView.inForce', { minutes: 50 }),
      ),
    ).toBeVisible();
  },
};

export const ChangesRequestedOnCorrectionNarrow320: Story = {
  ...ChangesRequestedOnCorrection,
  globals: { viewport: { value: 'narrow320' } },
};

export const Approved: Story = {
  args: { state: { status: 'ready', closeout: TUTOR_APPROVED } },
  play: async ({ canvasElement }) => {
    await showsHeading(
      canvasElement,
      i18n.t('closeout.tutorView.approved', { minutes: 50 }),
    );
    await targetsAreBigEnough(canvasElement);
  },
};

export const ApprovedNarrow320: Story = {
  ...Approved,
  globals: { viewport: { value: 'narrow320' } },
};

export const ResubmitAttendanceStep: Story = {
  args: {
    state: { status: 'ready', closeout: TUTOR_CHANGES_REQUESTED },
    attendance: { status: 'ready', attendance: ATTENDANCE_COMPLETED },
    editMode: 'resubmit',
  },
  play: async ({ canvasElement }) => {
    await expect(
      within(canvasElement).getByRole('button', {
        name: i18n.t('attendance.save'),
      }),
    ).toBeVisible();
    await onStep(canvasElement, 1);
  },
};

export const ResubmitAttendanceStepNarrow320: Story = {
  ...ResubmitAttendanceStep,
  globals: { viewport: { value: 'narrow320' } },
};

export const ResubmitReviewStep: Story = {
  args: {
    state: { status: 'ready', closeout: TUTOR_CHANGES_REQUESTED },
    attendance: { status: 'ready', attendance: ATTENDANCE_COMPLETED },
    editMode: 'resubmit',
    initialStep: 4,
    initialNote: 'Añadí la tarea y los siguientes pasos.',
  },
  play: async ({ canvasElement, args }) => {
    await onStep(canvasElement, 4);
    within(canvasElement)
      .getByRole('button', {
        name: i18n.t('closeout.tutorView.resubmitSubmit'),
      })
      .click();
    await expect(args.onResubmit).toHaveBeenCalledWith(
      {
        actual_minutes: 55,
        summary: SUMMARY,
        reason: 'Añadí la tarea y los siguientes pasos.',
      },
      'resubmit',
    );
  },
};

export const ResubmitReviewStepNarrow320: Story = {
  globals: { viewport: { value: 'narrow320' } },
  args: ResubmitReviewStep.args,
  play: async ({ canvasElement }) => onStep(canvasElement, 4),
};

export const CorrectionAttendanceStep: Story = {
  args: {
    state: { status: 'ready', closeout: TUTOR_APPROVED },
    attendance: { status: 'ready', attendance: ATTENDANCE_LOCKED },
    editMode: 'correction',
  },
  play: async ({ canvasElement }) => {
    await expect(
      within(canvasElement).getByText(
        i18n.t('attendance.blocked.attendance_locked'),
      ),
    ).toBeVisible();
    await expect(
      within(canvasElement).getByRole('button', {
        name: i18n.t('closeout.next'),
      }),
    ).toBeEnabled();
    await onStep(canvasElement, 1);
  },
};

export const CorrectionAttendanceStepNarrow320: Story = {
  ...CorrectionAttendanceStep,
  globals: { viewport: { value: 'narrow320' } },
};

export const CorrectionReasonError: Story = {
  args: {
    state: { status: 'ready', closeout: TUTOR_APPROVED },
    attendance: { status: 'ready', attendance: ATTENDANCE_LOCKED },
    editMode: 'correction',
    initialStep: 4,
  },
  play: async ({ canvasElement, args }) => {
    const canvas = within(canvasElement);
    canvas
      .getByRole('button', {
        name: i18n.t('closeout.tutorView.correctionSubmit'),
      })
      .click();
    await expect(
      await canvas.findByText(i18n.t('closeout.tutorView.reasonRequired')),
    ).toBeVisible();
    await expect(args.onResubmit).not.toHaveBeenCalled();
    await onStep(canvasElement, 4);
  },
};

export const CorrectionReasonErrorNarrow320: Story = {
  ...CorrectionReasonError,
  globals: { viewport: { value: 'narrow320' } },
};

export const NothingChanged: Story = {
  args: {
    state: { status: 'ready', closeout: TUTOR_APPROVED },
    attendance: { status: 'ready', attendance: ATTENDANCE_LOCKED },
    editMode: 'correction',
    initialStep: 4,
    initialNote: 'Salimos antes.',
    onResubmit: fn(() =>
      Promise.reject(new ApiError({ code: 'closeout_unchanged', status: 422 })),
    ),
  },
  play: async ({ canvasElement }) => {
    const canvas = within(canvasElement);
    canvas
      .getByRole('button', {
        name: i18n.t('closeout.tutorView.correctionSubmit'),
      })
      .click();
    await expect(await canvas.findByRole('alert')).toHaveTextContent(
      i18n.t('closeout.tutorView.unchanged'),
    );
    await fitsTheWidth(canvasElement);
  },
};

export const NothingChangedNarrow320: Story = {
  ...NothingChanged,
  globals: { viewport: { value: 'narrow320' } },
};
