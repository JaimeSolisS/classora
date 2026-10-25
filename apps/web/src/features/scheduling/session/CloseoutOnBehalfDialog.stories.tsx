import type { Meta, StoryObj } from '@storybook/react-vite';
import { expect, fn, userEvent, within } from 'storybook/test';
import { ApiError } from '@/app/api';
import { i18n } from '@/i18n';
import {
  ATTENDANCE_ABSENT,
  ATTENDANCE_MARKED,
  ATTENDANCE_UNMARKED,
  CHANGES_REQUESTED_RECORD,
} from '@/features/tutor-portal/closeout/fixtures';
import {
  CloseoutOnBehalfDialog,
  type CloseoutOnBehalfDialogProps,
} from './CloseoutOnBehalfDialog';

// "Close out on behalf" and "Take over and resubmit" (#301b H2): the submit
// mode, everyone absent (summary optional), incomplete attendance (the
// unmarked student named; Submit sends nothing), the takeover prefilled from
// the closeout and a server error with Reload, each at 320, 390 and 1280
// px. No request: the confirm is a stub. Axe runs in light and dark.

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
  desktop1280: {
    name: 'Desktop (1280 px)',
    styles: { width: '1280px', height: '800px' },
    type: 'desktop',
  },
} as const;

const meta = {
  title: 'Scheduling/CloseoutOnBehalfDialog',
  component: CloseoutOnBehalfDialog,
  args: {
    open: true,
    mode: 'submit',
    tutorName: 'Carmen Ruiz',
    scheduledMinutes: 60,
    allAbsent: false,
    attendance: { marked: 2, total: 2 },
    students: ATTENDANCE_MARKED.items,
    onConfirm: fn(() => Promise.resolve()),
    onCancel: fn(),
    onReload: fn(),
  },
  parameters: { viewport: { options: VIEWPORTS } },
  render: (args: CloseoutOnBehalfDialogProps) => (
    <main className="p-4">
      <CloseoutOnBehalfDialog {...args} />
    </main>
  ),
} satisfies Meta<typeof CloseoutOnBehalfDialog>;

export default meta;
type Story = StoryObj<typeof meta>;

async function dialog(): Promise<HTMLElement> {
  return within(document.body).findByRole('dialog');
}

const submit: Story = {
  play: async ({ args }) => {
    const box = within(await dialog());
    await expect(
      box.getByText(
        i18n.t('closeout.onBehalf.complete', { marked: 2, total: 2 }),
      ),
    ).toBeInTheDocument();
    await userEvent.type(
      box.getByLabelText(i18n.t('closeout.fields.topics'), { exact: false }),
      'Fracciones',
    );
    await userEvent.type(
      box.getByLabelText(i18n.t('closeout.fields.performance'), {
        exact: false,
      }),
      'Bien',
    );
    await userEvent.type(
      box.getByLabelText(i18n.t('closeout.fields.next_steps'), {
        exact: false,
      }),
      'Sumas',
    );
    await userEvent.type(
      box.getByLabelText(i18n.t('closeout.onBehalf.reason'), { exact: false }),
      'El tutor dejó el centro.',
    );
    await userEvent.click(
      box.getByRole('button', { name: i18n.t('closeout.onBehalf.submit') }),
    );
    await expect(args.onConfirm).toHaveBeenCalled();
  },
};
export const SubmitNarrow320: Story = {
  ...submit,
  globals: { viewport: { value: 'narrow320' } },
};
export const SubmitPhone390: Story = {
  ...submit,
  globals: { viewport: { value: 'phone390' } },
};
export const SubmitDesktop1280: Story = {
  ...submit,
  globals: { viewport: { value: 'desktop1280' } },
};

const allAbsent: Story = {
  args: { allAbsent: true, students: ATTENDANCE_ABSENT.items },
  play: async () => {
    const box = within(await dialog());
    await expect(box.getByText(i18n.t('closeout.allAbsent'))).toBeVisible();
  },
};
export const SubmitAllAbsentNarrow320: Story = {
  ...allAbsent,
  globals: { viewport: { value: 'narrow320' } },
};
export const SubmitAllAbsentPhone390: Story = {
  ...allAbsent,
  globals: { viewport: { value: 'phone390' } },
};
export const SubmitAllAbsentDesktop1280: Story = {
  ...allAbsent,
  globals: { viewport: { value: 'desktop1280' } },
};

const incomplete: Story = {
  args: {
    attendance: { marked: 1, total: 2 },
    students: ATTENDANCE_UNMARKED.items,
  },
  play: async ({ args }) => {
    const box = within(await dialog());
    await userEvent.click(
      box.getByRole('button', { name: i18n.t('closeout.onBehalf.submit') }),
    );
    const alert = box.getByRole('alert');
    await expect(alert).toHaveTextContent('Bruno Gómez');
    await expect(alert).toHaveFocus();
    await expect(args.onConfirm).not.toHaveBeenCalled();
  },
};
export const IncompleteAttendanceNarrow320: Story = {
  ...incomplete,
  globals: { viewport: { value: 'narrow320' } },
};
export const IncompleteAttendancePhone390: Story = {
  ...incomplete,
  globals: { viewport: { value: 'phone390' } },
};
export const IncompleteAttendanceDesktop1280: Story = {
  ...incomplete,
  globals: { viewport: { value: 'desktop1280' } },
};

const takeover: Story = {
  args: { mode: 'takeover', record: CHANGES_REQUESTED_RECORD },
  play: async () => {
    const box = within(await dialog());
    await expect(
      box.getByText(
        i18n.t('closeout.onBehalf.takeOverExplain', { tutor: 'Carmen Ruiz' }),
      ),
    ).toBeInTheDocument();
    await expect(
      box.getByLabelText(i18n.t('closeout.actualMinutes')),
    ).toHaveValue(55);
  },
};
export const TakeoverNarrow320: Story = {
  ...takeover,
  globals: { viewport: { value: 'narrow320' } },
};
export const TakeoverPhone390: Story = {
  ...takeover,
  globals: { viewport: { value: 'phone390' } },
};
export const TakeoverDesktop1280: Story = {
  ...takeover,
  globals: { viewport: { value: 'desktop1280' } },
};

const serverError: Story = {
  args: {
    mode: 'takeover',
    record: CHANGES_REQUESTED_RECORD,
    initialError: new ApiError({
      status: 409,
      code: 'closeout_not_with_tutor',
    }),
  },
  play: async () => {
    const box = within(await dialog());
    await expect(
      box.getByRole('button', { name: i18n.t('closeout.review.reload') }),
    ).toBeVisible();
  },
};
export const ServerErrorNarrow320: Story = {
  ...serverError,
  globals: { viewport: { value: 'narrow320' } },
};
export const ServerErrorPhone390: Story = {
  ...serverError,
  globals: { viewport: { value: 'phone390' } },
};
export const ServerErrorDesktop1280: Story = {
  ...serverError,
  globals: { viewport: { value: 'desktop1280' } },
};
