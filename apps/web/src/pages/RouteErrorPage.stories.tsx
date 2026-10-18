import type { Meta, StoryObj } from '@storybook/react-vite';
import { expect } from 'storybook/test';
import { ApiError } from '@/app/api';
import { i18n } from '@/i18n';
import { StoryRouter } from '@/test/StoryRouter';
import { RouteErrorPage } from './RouteErrorPage';

function ThrowsApiError(): never {
  throw new ApiError({
    code: 'internal_error',
    status: 500,
    requestId: 'req-5e1f0a2b',
  });
}

const meta = {
  title: 'Pages/RouteErrorPage',
  component: RouteErrorPage,
  render: () => (
    <StoryRouter
      routes={[
        {
          path: '/',
          element: <ThrowsApiError />,
          errorElement: <RouteErrorPage />,
        },
      ]}
    />
  ),
} satisfies Meta<typeof RouteErrorPage>;

export default meta;
type Story = StoryObj<typeof meta>;

// A page threw an ApiError while rendering: generic message plus reference.
export const WithReference: Story = {
  play: async ({ canvas }) => {
    await expect(
      canvas.getByRole('heading', {
        level: 1,
        name: i18n.t('routeError.title'),
      }),
    ).toBeVisible();
    await expect(canvas.getByText('req-5e1f0a2b')).toBeVisible();
    await expect(
      canvas.getByRole('button', { name: i18n.t('routeError.reload') }),
    ).toBeVisible();
  },
};
