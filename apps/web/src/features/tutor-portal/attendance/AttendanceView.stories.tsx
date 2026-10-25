import type { Meta, StoryObj } from '@storybook/react-vite';
import { expect, fn, userEvent, within } from 'storybook/test';
import { i18n } from '@/i18n';
import { AttendanceView } from './AttendanceView';
import {
  CORRECTED_ROW,
  LOCKED,
  NOT_STARTED,
  PARTIAL,
  UNMARKED,
  ZONE,
} from './fixtures';

// The tutor's attendance page (#76 H8): markable, partially marked, a row
// the center corrected, not started and locked (completed), at 320 px and
// 390 px. No request: saving is a stub. Axe runs in light and dark (the
// storybook projects).

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
  title: 'Tutor portal/AttendanceView',
  component: AttendanceView,
  args: {
    state: { status: 'ready', attendance: UNMARKED },
    timeZone: ZONE,
    onSave: fn(() => Promise.resolve()),
    onReload: fn(),
  },
  parameters: { viewport: { options: VIEWPORTS } },
  globals: { viewport: { value: 'phone390' } },
  render: (args) => (
    <main className="p-4">
      <h1 className="mb-4 text-2xl font-semibold">
        {i18n.t('attendance.title')}
      </h1>
      <AttendanceView {...args} />
    </main>
  ),
} satisfies Meta<typeof AttendanceView>;

export default meta;
type Story = StoryObj<typeof meta>;

async function fitsTheWidth(canvasElement: HTMLElement) {
  const main = canvasElement.querySelector('main')!;
  await expect(main.scrollWidth).toBeLessThanOrEqual(main.clientWidth);
}

export const Markable: Story = {
  play: async ({ canvasElement, args }) => {
    const canvas = within(canvasElement);
    const group = canvas.getByRole('group', { name: 'Ana Díaz' });
    await userEvent.click(
      within(group).getByRole('radio', {
        name: i18n.t('attendance.statuses.present'),
      }),
    );
    await userEvent.click(
      canvas.getByRole('button', { name: i18n.t('attendance.save') }),
    );
    await expect(args.onSave).toHaveBeenCalledWith([
      { student_id: UNMARKED.items[0]!.student.id, status: 'present' },
    ]);
    await expect(
      await canvas.findByText(i18n.t('attendance.saved')),
    ).toBeInTheDocument();
    await fitsTheWidth(canvasElement);
  },
};

export const Narrow320: Story = {
  globals: { viewport: { value: 'narrow320' } },
  args: { state: { status: 'ready', attendance: PARTIAL } },
  play: async ({ canvasElement }) => {
    await fitsTheWidth(canvasElement);
    const save = within(canvasElement).getByRole('button', {
      name: i18n.t('attendance.save'),
    });
    const box = save.getBoundingClientRect();
    await expect(box.height).toBeGreaterThanOrEqual(24);
  },
};

export const PartiallyMarked: Story = {
  args: { state: { status: 'ready', attendance: PARTIAL } },
};

export const CorrectedRow: Story = {
  args: { state: { status: 'ready', attendance: CORRECTED_ROW } },
  play: async ({ canvasElement }) => {
    const canvas = within(canvasElement);
    await expect(
      canvas.getByText(i18n.t('attendance.corrected')),
    ).toBeInTheDocument();
    await expect(
      canvas.queryByRole('group', { name: 'Bruno Gómez' }),
    ).toBeNull();
  },
};

export const NotStarted: Story = {
  args: { state: { status: 'ready', attendance: NOT_STARTED } },
  play: async ({ canvasElement }) => {
    await expect(
      within(canvasElement).queryByRole('button', {
        name: i18n.t('attendance.save'),
      }),
    ).toBeNull();
  },
};

export const Locked: Story = {
  args: { state: { status: 'ready', attendance: LOCKED } },
  play: async ({ canvasElement }) => {
    await expect(
      within(canvasElement).getByText(
        i18n.t('attendance.blocked.attendance_locked'),
      ),
    ).toBeInTheDocument();
  },
};

export const LockedNarrow320: Story = {
  globals: { viewport: { value: 'narrow320' } },
  args: { state: { status: 'ready', attendance: LOCKED } },
  play: async ({ canvasElement }) => fitsTheWidth(canvasElement),
};

export const Loading: Story = {
  args: { state: { status: 'loading' } },
};
