import type { Meta, StoryObj } from '@storybook/react-vite';
import { expect, fn } from 'storybook/test';
import { ApiError } from '@/app/api';
import { i18n } from '@/i18n';
import { StoryRouter } from '@/test/StoryRouter';
import { COMMITTED_ROWS, rowPage, STAGED_ROWS, STUDENT_ID } from './fixtures';
import { ImportRowsTable } from './ImportRowsTable';

// The rows of an import's report (M8-05b C2, D1): a staged report (ready,
// duplicate and error rows, with an unknown code), the rows after the
// commit (no values, links to the records), empty, loading, an error and
// a 320 px phone. No request.

const meta = {
  title: 'Imports/ImportRowsTable',
  component: ImportRowsTable,
  args: {
    state: { status: 'ready', page: rowPage(STAGED_ROWS) },
    kind: 'students',
    labelledBy: 'rows-title',
    onPage: fn(),
  },
  render: (args) => (
    <StoryRouter
      routes={[
        {
          path: '*',
          element: (
            <>
              <h2 id="rows-title">{i18n.t('imports.rows.title')}</h2>
              <ImportRowsTable {...args} />
            </>
          ),
        },
      ]}
    />
  ),
} satisfies Meta<typeof ImportRowsTable>;

export default meta;
type Story = StoryObj<typeof meta>;

export const Staged: Story = {
  play: async ({ canvas }) => {
    await expect(canvas.getAllByRole('row')).toHaveLength(
      STAGED_ROWS.length + 1,
    );
    // Every status is a word, not only a border (D1).
    await expect(
      canvas.getByText(i18n.t('imports.rowStatus.error')),
    ).toBeVisible();
    await expect(
      canvas.getByText(i18n.t('imports.rowStatus.duplicate')),
    ).toBeVisible();
  },
};

export const AfterCommit: Story = {
  args: { state: { status: 'ready', page: rowPage(COMMITTED_ROWS) } },
  play: async ({ canvasElement }) => {
    await expect(
      canvasElement.querySelector(`a[href="/students/${STUDENT_ID}"]`),
    ).not.toBeNull();
  },
};

export const Empty: Story = {
  args: { state: { status: 'ready', page: rowPage([]) } },
  play: async ({ canvas }) => {
    await expect(canvas.getByText(i18n.t('imports.rows.empty'))).toBeVisible();
    await expect(canvas.queryByRole('table')).toBeNull();
  },
};

export const Loading: Story = {
  args: { state: { status: 'loading' } },
};

export const LoadError: Story = {
  args: {
    state: {
      status: 'error',
      error: new ApiError({
        status: 500,
        code: 'internal_error',
        requestId: 'req-story',
      }),
      onRetry: fn(),
    },
  },
  play: async ({ canvas }) => {
    await expect(canvas.getByRole('alert')).toBeVisible();
  },
};

/** A phone (320 px): the table scrolls inside its container (D1). */
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
