import type { Meta, StoryObj } from '@storybook/react-vite';
import { expect, fn } from 'storybook/test';
import { ApiError } from '@/app/api';
import { i18n } from '@/i18n';
import { ClassroomsTable } from './ClassroomsTable';
import { AULA_1, CLASSROOMS } from './fixtures';

// The classrooms table (#39 D2, D6): mixed status and summaries (weekly
// windows and "No restriction"), empty, loading and error. No request.

const meta = {
  title: 'Catalog/ClassroomsTable',
  component: ClassroomsTable,
  args: {
    state: {
      status: 'ready',
      page: {
        items: CLASSROOMS,
        total: CLASSROOMS.length,
        limit: 25,
        offset: 0,
      },
    },
    labelledBy: 'classrooms-title',
    onPage: fn(),
    onEdit: fn(),
  },
  render: (args) => (
    <>
      <h1 id="classrooms-title">{i18n.t('catalog.classrooms.title')}</h1>
      <ClassroomsTable {...args} />
    </>
  ),
} satisfies Meta<typeof ClassroomsTable>;

export default meta;
type Story = StoryObj<typeof meta>;

export const Classrooms: Story = {
  play: async ({ canvas, userEvent, args }) => {
    await expect(
      canvas.getByRole('table', { name: i18n.t('catalog.classrooms.title') }),
    ).toBeVisible();
    await expect(canvas.getAllByRole('row')).toHaveLength(
      CLASSROOMS.length + 1,
    );
    await expect(
      canvas.getByText(i18n.t('weeklyWindows.unrestricted')),
    ).toBeVisible();
    await expect(
      canvas.getByText(i18n.t('catalog.status.inactive')),
    ).toBeVisible();
    await userEvent.click(
      canvas.getByRole('button', {
        name: i18n.t('catalog.actions.editNamed', { name: AULA_1.name }),
      }),
    );
    await expect(args.onEdit).toHaveBeenCalledWith(AULA_1);
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
      canvas.getByText(i18n.t('catalog.classrooms.empty')),
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
