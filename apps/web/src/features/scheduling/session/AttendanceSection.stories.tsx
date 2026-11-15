import type { Meta, StoryObj } from '@storybook/react-vite';
import { expect, fn, userEvent, within } from 'storybook/test';
import { ApiError } from '@/app/api';
import { i18n } from '@/i18n';
import type { SessionAttendance } from '@/features/tutor-portal/attendance/api';
import {
  NOT_STARTED,
  STAFF_VIEW,
  ZONE,
} from '@/features/tutor-portal/attendance/fixtures';
import { AttendanceSection } from './AttendanceSection';
import { CorrectAttendanceDialog } from './CorrectAttendanceDialog';

// The staff session page's attendance section and the correction dialog
// (#76 H8): with Correct buttons, read-only, not started, and the dialog
// with a version conflict. #311: both variants at 320 px and 390 px with a
// 60-character name and a corrected row; the table scrolls inside its
// focusable region and the page never scrolls sideways. No request: the
// correction is a stub. Axe runs in light and dark (the storybook projects).

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

const LONG_NAME =
  'Maximilianodelossantosgutierrezfernandezvillalobosrodriguezz';

/** The staff view with a 60-character name (no spaces) and a corrected row. */
const LONG_NAMES: SessionAttendance = {
  ...STAFF_VIEW,
  items: STAFF_VIEW.items.map((row, index) =>
    index === 0
      ? { ...row, student: { ...row.student, display_name: LONG_NAME } }
      : row,
  ),
};

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
  parameters: { viewport: { options: VIEWPORTS } },
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

// #311 B2: the region (named by the heading, tabindex 0) is the only
// horizontal scroller. At 320 px and 390 px the 32 rem table is wider than
// the region, so it really scrolls; the page doesn't.
async function scrollsInRegionOnly(canvasElement: HTMLElement) {
  const region = within(canvasElement).getByRole('region', {
    name: i18n.t('attendance.section.heading'),
  });
  await expect(region).toHaveAttribute('tabindex', '0');
  await expect(region.scrollWidth).toBeGreaterThan(region.clientWidth);
  const page = canvasElement.ownerDocument.documentElement;
  await expect(page.scrollWidth).toBeLessThanOrEqual(page.clientWidth);
}

const longNames = {
  state: { status: 'ready', attendance: LONG_NAMES },
} as const;

export const WithCorrectNarrow320: Story = {
  args: longNames,
  globals: { viewport: { value: 'narrow320' } },
  play: async ({ canvasElement }) => {
    await scrollsInRegionOnly(canvasElement);
    await expect(
      within(canvasElement).getByRole('button', {
        name: i18n.t('attendance.section.correctFor', { name: LONG_NAME }),
      }),
    ).toBeInTheDocument();
  },
};

export const WithCorrectPhone390: Story = {
  ...WithCorrectNarrow320,
  globals: { viewport: { value: 'phone390' } },
};

export const ReadOnlyNarrow320: Story = {
  args: { ...longNames, canCorrect: false },
  globals: { viewport: { value: 'narrow320' } },
  play: async ({ canvasElement }) => {
    await scrollsInRegionOnly(canvasElement);
    await expect(
      within(canvasElement).queryByRole('button', {
        name: i18n.t('attendance.section.correctFor', { name: LONG_NAME }),
      }),
    ).toBeNull();
  },
};

export const ReadOnlyPhone390: Story = {
  ...ReadOnlyNarrow320,
  globals: { viewport: { value: 'phone390' } },
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
