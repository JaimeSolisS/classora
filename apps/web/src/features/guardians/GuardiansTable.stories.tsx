import type { Meta, StoryObj } from '@storybook/react-vite';
import { expect, fn } from 'storybook/test';
import { ApiError } from '@/app/api';
import { i18n } from '@/i18n';
import { StoryRouter } from '@/test/StoryRouter';
import { guardianPage, LIST_ITEMS } from './fixtures';
import { GuardiansTable } from './GuardiansTable';

// The guardian list (#34 I1, J3): mixed rows (account, linked students,
// status), empty, loading and error. No request.

const meta = {
  title: 'Guardians/GuardiansTable',
  component: GuardiansTable,
  args: {
    state: { status: 'ready', page: guardianPage(LIST_ITEMS, 30) },
    labelledBy: 'guardians-title',
    searching: false,
    onPage: fn(),
  },
  render: (args) => (
    <StoryRouter
      routes={[
        {
          path: '*',
          element: (
            <>
              <h1 id="guardians-title">{i18n.t('guardians.title')}</h1>
              <GuardiansTable {...args} />
            </>
          ),
        },
      ]}
    />
  ),
} satisfies Meta<typeof GuardiansTable>;

export default meta;
type Story = StoryObj<typeof meta>;

export const MixedRows: Story = {
  play: async ({ canvas }) => {
    await expect(
      canvas.getByRole('table', { name: i18n.t('guardians.title') }),
    ).toBeVisible();
    await expect(canvas.getAllByRole('row')).toHaveLength(
      LIST_ITEMS.length + 1,
    );
    await expect(
      canvas.getByText(i18n.t('guardians.account.has')),
    ).toBeVisible();
  },
};

export const Empty: Story = {
  args: { state: { status: 'ready', page: guardianPage([]) } },
  play: async ({ canvas }) => {
    await expect(
      canvas.getByText(i18n.t('guardians.list.empty')),
    ).toBeVisible();
  },
};

export const Loading: Story = {
  args: { state: { status: 'loading' } },
};

export const LoadError: Story = {
  args: {
    state: {
      status: 'error',
      error: new ApiError({ code: 'internal_error', status: 500 }),
      onRetry: fn(),
    },
  },
  play: async ({ canvas }) => {
    await expect(canvas.getByRole('alert')).toBeVisible();
  },
};
