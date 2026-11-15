import type { Meta, StoryObj } from '@storybook/react-vite';
import { expect, fn } from 'storybook/test';
import { ApiError } from '@/app/api';
import { i18n } from '@/i18n';
import { StoryRouter } from '@/test/StoryRouter';
import { LIST_ITEMS } from './fixtures';
import { UsersTable } from './UsersTable';

// The user list (#31 J3, L3): rows, empty, loading and error. No request.

const meta = {
  title: 'Users/UsersTable',
  component: UsersTable,
  args: {
    state: {
      status: 'ready',
      page: { items: LIST_ITEMS, total: 30, limit: 25, offset: 0 },
    },
    labelledBy: 'users-title',
    onPage: fn(),
  },
  render: (args) => (
    <StoryRouter
      routes={[
        {
          path: '*',
          element: (
            <>
              <h1 id="users-title">{i18n.t('users.title')}</h1>
              <UsersTable {...args} />
            </>
          ),
        },
      ]}
    />
  ),
} satisfies Meta<typeof UsersTable>;

export default meta;
type Story = StoryObj<typeof meta>;

export const Rows: Story = {
  play: async ({ canvas, userEvent, args }) => {
    await expect(
      canvas.getByRole('table', { name: i18n.t('users.title') }),
    ).toBeVisible();
    await expect(canvas.getAllByRole('row')).toHaveLength(4);
    await expect(
      canvas.getByRole('link', { name: 'Ana Tutor' }),
    ).toHaveAttribute('href', `/users/${LIST_ITEMS[0]!.id}`);
    await expect(
      canvas.getByText(i18n.t('userStates.suspended')),
    ).toBeVisible();
    await expect(
      canvas.getByRole('button', { name: i18n.t('users.list.previous') }),
    ).toBeDisabled();
    await userEvent.click(
      canvas.getByRole('button', { name: i18n.t('users.list.next') }),
    );
    await expect(args.onPage).toHaveBeenCalledWith(2);
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
    await expect(canvas.getByText(i18n.t('users.list.empty'))).toBeVisible();
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
