import type { Meta, StoryObj } from '@storybook/react-vite';
import { expect } from 'storybook/test';
import { i18n } from '@/i18n';
import { StoryRouter } from '@/test/StoryRouter';
import { NotFoundPage } from './NotFoundPage';

const meta = {
  title: 'Pages/NotFoundPage',
  component: NotFoundPage,
  render: () => (
    <StoryRouter
      path="/does-not-exist"
      routes={[{ path: '*', element: <NotFoundPage /> }]}
    />
  ),
} satisfies Meta<typeof NotFoundPage>;

export default meta;
type Story = StoryObj<typeof meta>;

export const Default: Story = {
  play: async ({ canvas }) => {
    await expect(
      canvas.getByRole('heading', { level: 1, name: i18n.t('notFound.title') }),
    ).toBeVisible();
    await expect(
      canvas.getByRole('link', { name: i18n.t('notFound.backHome') }),
    ).toHaveAttribute('href', '/');
  },
};
