import type { Meta, StoryObj } from '@storybook/react-vite';
import { expect, fn, userEvent } from 'storybook/test';
import { i18n } from '@/i18n';
import type { LocalDate } from '@/lib/time';
import { StoryQueryClient } from '@/test/StoryQueryClient';
import { ExportForm } from './ExportForm';
import type { ExportFormLoaders } from './loaders';
import { ALL_DATASETS, QUEUED } from './fixtures';

// The request form (M8-07b B1, B2, E3): class sessions (the personal hint,
// the date range, the pickers), expenses (no hint), the column reordering
// and a client range check. Stub loaders and submit: no request.

const LOADERS: ExportFormLoaders = {
  searchTutors: () =>
    Promise.resolve({
      items: [{ id: 'tutor-1', name: 'Ana Tutora', inactive: false }],
      total: 1,
    }),
  searchStudents: () =>
    Promise.resolve({
      items: [{ id: 'student-1', name: 'Luis Alumno', inactive: false }],
      total: 1,
    }),
  fetchRooms: () =>
    Promise.resolve([{ id: 'room-1', name: 'Aula 1', inactive: false }]),
  fetchLevels: () =>
    Promise.resolve({
      items: [],
      total: 0,
      limit: 100,
      offset: 0,
    } as unknown as Awaited<ReturnType<ExportFormLoaders['fetchLevels']>>),
};

const meta = {
  title: 'Exports/ExportForm',
  component: ExportForm,
  args: {
    datasets: ALL_DATASETS,
    initialDataset: 'class_sessions',
    today: '2026-11-05' as LocalDate,
    onRequested: fn(),
    loaders: LOADERS,
    submit: fn(() => Promise.resolve(QUEUED)),
  },
  render: (args) => (
    <StoryQueryClient>
      <ExportForm {...args} />
    </StoryQueryClient>
  ),
} satisfies Meta<typeof ExportForm>;

export default meta;
type Story = StoryObj<typeof meta>;

export const ClassSessions: Story = {
  play: async ({ canvas }) => {
    await expect(
      canvas.getByText(i18n.t('exports.form.personalHint')),
    ).toBeVisible();
    await expect(
      canvas.getByLabelText(i18n.t('exports.form.from')),
    ).toHaveValue('2026-11-01');
    await expect(canvas.getByLabelText(i18n.t('exports.form.to'))).toHaveValue(
      '2026-11-30',
    );
  },
};

export const Expenses: Story = {
  args: { initialDataset: 'expenses' },
  play: async ({ canvas }) => {
    await expect(
      canvas.queryByText(i18n.t('exports.form.personalHint')),
    ).toBeNull();
  },
};

/** "Up" moves a column and keeps the keyboard on it. */
export const Reordered: Story = {
  args: { initialDataset: 'students' },
  play: async ({ canvas, canvasElement }) => {
    const up = canvas.getByRole('button', {
      name: i18n.t('exports.form.moveUpFor', { column: 'Given names' }),
    });
    await userEvent.click(up);
    const order = [...canvasElement.querySelectorAll('li[data-column]')].map(
      (li) => li.getAttribute('data-column'),
    );
    await expect(order.slice(0, 2)).toEqual(['given_names', 'family_names']);
    await expect(canvasElement.ownerDocument.activeElement).toBe(
      canvas.getByRole('button', {
        name: i18n.t('exports.form.moveDownFor', { column: 'Given names' }),
      }),
    );
  },
};

/** Every column unchecked: the client check, and nothing is sent. */
export const NoColumns: Story = {
  args: { initialDataset: 'students' },
  play: async ({ args, canvasElement, canvas }) => {
    for (const box of canvasElement.querySelectorAll<HTMLInputElement>(
      'li[data-column] input[type="checkbox"]',
    )) {
      await userEvent.click(box);
    }
    await userEvent.click(
      canvas.getByRole('button', { name: i18n.t('exports.form.submit') }),
    );
    await expect(
      canvas.getByText(i18n.t('exports.validation.noColumns')),
    ).toBeVisible();
    await expect(args.submit).not.toHaveBeenCalled();
  },
};

/** A phone (320 px): no sideways scrolling. */
export const Mobile: Story = {
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
  globals: { viewport: { value: 'narrow320' } },
  play: async ({ canvasElement }) => {
    const root = canvasElement.ownerDocument.documentElement;
    await expect(root.scrollWidth).toBeLessThanOrEqual(root.clientWidth);
  },
};
