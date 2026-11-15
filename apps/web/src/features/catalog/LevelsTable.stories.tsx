import type { Meta, StoryObj } from '@storybook/react-vite';
import { expect, fn } from 'storybook/test';
import { ApiError } from '@/app/api';
import { i18n } from '@/i18n';
import { StoryRouter } from '@/test/StoryRouter';
import { LEVELS, SECUNDARIA } from './fixtures';
import { LevelsTable } from './LevelsTable';

// The education levels table (#32 H2, J4): rows with mixed status, empty,
// loading and error. No request.

const meta = {
  title: 'Catalog/LevelsTable',
  component: LevelsTable,
  args: {
    state: {
      status: 'ready',
      page: { items: LEVELS, total: 30, limit: 25, offset: 0 },
    },
    labelledBy: 'levels-title',
    onPage: fn(),
    onEdit: fn(),
  },
  render: (args) => (
    <StoryRouter
      routes={[
        {
          path: '*',
          element: (
            <>
              <h1 id="levels-title">{i18n.t('catalog.title')}</h1>
              <LevelsTable {...args} />
            </>
          ),
        },
      ]}
    />
  ),
} satisfies Meta<typeof LevelsTable>;

export default meta;
type Story = StoryObj<typeof meta>;

export const Rows: Story = {
  play: async ({ canvas, userEvent, args }) => {
    await expect(
      canvas.getByRole('table', { name: i18n.t('catalog.title') }),
    ).toBeVisible();
    await expect(canvas.getAllByRole('row')).toHaveLength(LEVELS.length + 1);
    await expect(
      canvas.getByRole('link', { name: 'Secundaria' }),
    ).toHaveAttribute('href', `/catalog/education-levels/${SECUNDARIA.id}`);
    await expect(
      canvas.getByText(i18n.t('catalog.status.inactive')),
    ).toBeVisible();
    await userEvent.click(
      canvas.getByRole('button', {
        name: i18n.t('catalog.actions.editNamed', { name: 'Secundaria' }),
      }),
    );
    await expect(args.onEdit).toHaveBeenCalledWith(SECUNDARIA);
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
    await expect(
      canvas.getByText(i18n.t('catalog.levels.empty')),
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
