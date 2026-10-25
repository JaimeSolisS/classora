import type { Meta, StoryObj } from '@storybook/react-vite';
import { expect, fn, userEvent, within } from 'storybook/test';
import { ApiError } from '@/app/api';
import { i18n } from '@/i18n';
import {
  NOT_STARTED,
  STAFF_VIEW,
  ZONE,
} from '@/features/tutor-portal/attendance/fixtures';
import { AttendanceSection } from './AttendanceSection';
import { CorrectAttendanceDialog } from './CorrectAttendanceDialog';

// The staff session page's attendance section and the correction dialog
// (#76 H8): with Correct buttons, read-only, not started, and the dialog
// with a version conflict. No request: the correction is a stub. Axe runs
// in light and dark (the storybook projects).

const meta = {
  title: 'Scheduling/AttendanceSection',
  component: AttendanceSection,
  args: {
    state: { status: 'ready', attendance: STAFF_VIEW },
    timeZone: ZONE,
    canCorrect: true,
    onCorrect: fn(() => Promise.resolve()),
    onReload: fn(),
  },
  render: (args) => (
    <main className="p-4">
      <AttendanceSection {...args} />
    </main>
  ),
} satisfies Meta<typeof AttendanceSection>;

export default meta;
type Story = StoryObj<typeof meta>;

export const WithCorrect: Story = {
  play: async ({ canvasElement, args }) => {
    const canvas = within(canvasElement);
    await userEvent.click(
      canvas.getByRole('button', {
        name: i18n.t('attendance.section.correctFor', { name: 'Ana Díaz' }),
      }),
    );
    const dialog = await within(document.body).findByRole('dialog');
    await userEvent.type(
      within(dialog).getByLabelText(i18n.t('attendance.correctDialog.reason')),
      'Salió temprano',
    );
    await userEvent.click(
      within(dialog).getByRole('button', {
        name: i18n.t('attendance.correctDialog.submit'),
      }),
    );
    await expect(args.onCorrect).toHaveBeenCalled();
  },
};

export const ReadOnly: Story = {
  args: { canCorrect: false },
};

export const NotStarted: Story = {
  args: { state: { status: 'ready', attendance: NOT_STARTED } },
};

export const DialogWithConflict: Story = {
  render: () => (
    <main className="p-4">
      <CorrectAttendanceDialog
        item={STAFF_VIEW.items[0]!}
        onConfirm={() => Promise.resolve()}
        onCancel={() => undefined}
        onReload={() => undefined}
        initialReason="Salió temprano"
        initialError={new ApiError({ status: 409, code: 'version_conflict' })}
      />
    </main>
  ),
  play: async () => {
    const dialog = await within(document.body).findByRole('dialog');
    await expect(
      within(dialog).getByText(i18n.t('attendance.conflict')),
    ).toBeInTheDocument();
  },
};
