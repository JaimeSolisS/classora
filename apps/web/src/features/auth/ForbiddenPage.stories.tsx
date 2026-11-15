import type { Meta, StoryObj } from '@storybook/react-vite';
import { expect } from 'storybook/test';
import { i18n } from '@/i18n';
import { StoryRouter } from '@/test/StoryRouter';
import { ForbiddenPage } from './ForbiddenPage';

const meta = {
  title: 'Auth/ForbiddenPage',
  component: ForbiddenPage,
  render: () => (
    <StoryRouter
      path="/audit"
      routes={[{ path: '*', element: <ForbiddenPage /> }]}
    />
  ),
} satisfies Meta<typeof ForbiddenPage>;

export default meta;
type Story = StoryObj<typeof meta>;

export const Default: Story = {
  play: async ({ canvas }) => {
    await expect(
      canvas.getByRole('heading', {
        level: 1,
        name: i18n.t('auth.forbidden.title'),
      }),
    ).toBeVisible();
    await expect(
      canvas.getByRole('link', { name: i18n.t('auth.forbidden.backHome') }),
    ).toHaveAttribute('href', '/');
  },
};
