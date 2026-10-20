import type { Meta, StoryObj } from '@storybook/react-vite';
import { expect, fn } from 'storybook/test';
import { ApiError } from '@/app/api';
import { i18n } from '@/i18n';
import type { AcademicPeriod, Closure } from './api';
import { CLOSURES, PERIODS, WINTER } from './fixtures';
import { RangesTable, type RangeRow } from './RangesTable';

// The academic periods and closures tables (#38 E6): mixed status, empty,
// loading and error. No request.

const periodRows = PERIODS.map((period: AcademicPeriod) => ({
  ...period,
  typeLabel: i18n.t(`catalog.periodTypes.${period.period_type}`),
}));
const closureRows = CLOSURES.map((closure: Closure) => ({
  ...closure,
  typeLabel: i18n.t(`catalog.closureTypes.${closure.closure_type}`),
}));

const meta = {
  title: 'Catalog/RangesTable',
  component: RangesTable<RangeRow>,
  args: {
    state: {
      status: 'ready',
      page: {
        items: periodRows,
        total: periodRows.length,
        limit: 25,
        offset: 0,
      },
    },
    dates: 'split',
    labelledBy: 'ranges-title',
    emptyText: i18n.t('catalog.periods.empty'),
    onPage: fn(),
    onEdit: fn(),
  },
  render: (args) => (
    <>
      <h1 id="ranges-title">{i18n.t('catalog.periods.title')}</h1>
      <RangesTable {...args} />
    </>
  ),
} satisfies Meta<typeof RangesTable<RangeRow>>;

export default meta;
type Story = StoryObj<typeof meta>;

export const Periods: Story = {
  play: async ({ canvas, userEvent, args }) => {
    await expect(
      canvas.getByRole('table', { name: i18n.t('catalog.periods.title') }),
    ).toBeVisible();
    await expect(canvas.getAllByRole('row')).toHaveLength(PERIODS.length + 1);
    await expect(
      canvas.getByRole('columnheader', {
        name: i18n.t('catalog.range.endsOn'),
      }),
    ).toBeVisible();
    await expect(
      canvas.getByText(i18n.t('catalog.status.inactive')),
    ).toBeVisible();
    await userEvent.click(
      canvas.getByRole('button', {
        name: i18n.t('catalog.actions.editNamed', { name: PERIODS[0]!.name }),
      }),
    );
    await expect(args.onEdit).toHaveBeenCalledWith(periodRows[0]);
  },
};

export const Closures: Story = {
  args: {
    state: {
      status: 'ready',
      page: {
        items: closureRows,
        total: closureRows.length,
        limit: 25,
        offset: 0,
      },
    },
    dates: 'combined',
  },
  play: async ({ canvas }) => {
    await expect(
      canvas.getByRole('columnheader', { name: i18n.t('catalog.range.dates') }),
    ).toBeVisible();
    await expect(canvas.getByText(WINTER.name)).toBeVisible();
    await expect(
      canvas.getByText(i18n.t('catalog.closureTypes.vacation')),
    ).toBeVisible();
  },
};

export const Empty: Story = {
  args: {
    state: {
      status: 'ready',
      page: { items: [], total: 0, limit: 25, offset: 0 },
    },
  },
  play: async ({ canvas }) => {
    await expect(
      canvas.getByText(i18n.t('catalog.periods.empty')),
    ).toBeVisible();
    await expect(canvas.queryByRole('table')).toBeNull();
  },
};

export const Loading: Story = {
  args: { state: { status: 'loading' } },
  play: async ({ canvasElement }) => {
    await expect(
      canvasElement.querySelector('[aria-busy="true"]'),
    ).not.toBeNull();
  },
};

export const LoadError: Story = {
  args: {
    state: {
      status: 'error',
      error: new ApiError({
        code: 'internal_error',
        status: 500,
        requestId: 'req-story',
      }),
      onRetry: fn(),
    },
  },
  play: async ({ canvas }) => {
    await expect(canvas.getByRole('alert')).toHaveTextContent(
      i18n.t('errors:internal_error'),
    );
  },
};
