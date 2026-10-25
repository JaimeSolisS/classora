import type { Meta, StoryObj } from '@storybook/react-vite';
import { expect, fn, within } from 'storybook/test';
import { i18n } from '@/i18n';
import { CloseoutFlow } from './CloseoutFlow';
import {
  ATTENDANCE_MARKED,
  ATTENDANCE_UNMARKED,
  NOT_STARTED,
  OPEN,
  SUBMITTED,
  SUMMARY,
  ZONE,
} from './fixtures';

// The tutor's closeout flow (#77 H7): each step at 390 px and 320 px, the
// review, the read-only view and the not-started block. No request: the
// callbacks are stubs. Axe runs in light and dark (the storybook projects).

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
