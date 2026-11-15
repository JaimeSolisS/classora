import type { Meta, StoryObj } from '@storybook/react-vite';
import { useState } from 'react';
import { expect, fn } from 'storybook/test';
import { i18n } from '@/i18n';
import { WeeklyWindowsEditor } from './WeeklyWindowsEditor';
import { validateWindows, type WindowRow } from './windows';

// The weekly windows editor (#39 D3, D6; reused by #45): windows, the empty
// state, and an overlapping window with its row error. Controlled: the
// story keeps the rows in state.

const ROWS: WindowRow[] = [
  { key: 'a', weekday: 1, startsAt: '09:00', endsAt: '12:00' },
  { key: 'b', weekday: 1, startsAt: '12:00', endsAt: '14:00' },
  { key: 'c', weekday: 1, startsAt: '11:00', endsAt: '13:00' },
];

function Stateful({
  initial,
  showErrors,
}: {
  initial: WindowRow[];
  showErrors: boolean;
}) {
  const [rows, setRows] = useState(initial);
  return (
    <WeeklyWindowsEditor
      id="windows"
      rows={rows}
      onChange={setRows}
      errors={showErrors ? validateWindows(rows) : []}
    />
  );
}

const meta = {
  title: 'Components/WeeklyWindowsEditor',
  component: WeeklyWindowsEditor,
  args: { id: 'windows', rows: ROWS.slice(0, 2), onChange: fn() },
  render: (args) => (
    <div className="max-w-2xl">
      <Stateful
        initial={[...args.rows]}
        showErrors={args.errors !== undefined}
      />
    </div>
  ),
} satisfies Meta<typeof WeeklyWindowsEditor>;

export default meta;
type Story = StoryObj<typeof meta>;

export const Windows: Story = {
  play: async ({ canvas, userEvent }) => {
    await expect(
      canvas.getByRole('group', { name: i18n.t('weeklyWindows.legend') }),
    ).toBeVisible();
    await userEvent.click(
      canvas.getByRole('button', { name: i18n.t('weeklyWindows.add') }),
    );
    await expect(
      canvas.getByLabelText(
        i18n.t('weeklyWindows.weekdayLabel', { number: 3 }),
      ),
    ).toHaveFocus();
    await userEvent.click(
      canvas.getByRole('button', {
        name: i18n.t('weeklyWindows.removeNamed', { number: 3 }),
      }),
    );
    await expect(
      canvas.getByRole('button', { name: i18n.t('weeklyWindows.add') }),
    ).toHaveFocus();
  },
};

export const Empty: Story = {
  args: { rows: [] },
  play: async ({ canvas }) => {
    await expect(canvas.getByText(i18n.t('weeklyWindows.empty'))).toBeVisible();
  },
};

export const Overlap: Story = {
  args: { rows: ROWS, errors: [] },
  play: async ({ canvas }) => {
    const start = canvas.getByLabelText(
      i18n.t('weeklyWindows.startsAtLabel', { number: 3 }),
    );
    await expect(start).toHaveAttribute('aria-invalid', 'true');
    await expect(start).toHaveAccessibleDescription(
      i18n.t('weeklyWindows.validation.overlap'),
    );
  },
};
