import type { Meta, StoryObj } from '@storybook/react-vite';
import { expect, fn } from 'storybook/test';
import { ApiError } from '@/app/api';
import { i18n } from '@/i18n';
import { StoryRouter } from '@/test/StoryRouter';
import { FamiliesTable } from './FamiliesTable';
import { familyPage, LIST_ITEMS } from './fixtures';

// The family list (#172 H1, I2): rows (student count, status), empty and
// error. No request.

const meta = {
  title: 'Families/FamiliesTable',
  component: FamiliesTable,
  args: {
    state: { status: 'ready', page: familyPage(LIST_ITEMS, 30) },
    labelledBy: 'families-title',
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
              <h1 id="families-title">{i18n.t('families.title')}</h1>
              <FamiliesTable {...args} />
            </>
          ),
        },
      ]}
    />
  ),
} satisfies Meta<typeof FamiliesTable>;

export default meta;
type Story = StoryObj<typeof meta>;

export const Rows: Story = {
  play: async ({ canvas }) => {
    await expect(
      canvas.getByRole('table', { name: i18n.t('families.title') }),
    ).toBeVisible();
    await expect(canvas.getAllByRole('row')).toHaveLength(
      LIST_ITEMS.length + 1,
    );
    await expect(
      canvas.getByText(i18n.t('families.status.inactive')),
    ).toBeVisible();
  },
};

export const Empty: Story = {
  args: { state: { status: 'ready', page: familyPage([]) } },
  play: async ({ canvas }) => {
    await expect(canvas.getByText(i18n.t('families.list.empty'))).toBeVisible();
  },
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
