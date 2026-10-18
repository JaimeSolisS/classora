import type { Meta, StoryObj } from '@storybook/react-vite';
import { expect, fn } from 'storybook/test';
import { ApiError } from '@/app/api';
import { i18n } from '@/i18n';
import { ErrorAlert } from './ErrorAlert';

const notFound = new ApiError({
  code: 'not_found',
  status: 404,
  requestId: '3f2a9c1e-7b44-4d1a-9e0b-2c6f5d8a1b90',
});

const meta = {
  title: 'Feedback/ErrorAlert',
  component: ErrorAlert,
  args: { error: notFound },
} satisfies Meta<typeof ErrorAlert>;

export default meta;
type Story = StoryObj<typeof meta>;

// A Problem Details error with its request ID as the support reference.
export const ProblemWithReference: Story = {
  play: async ({ canvas }) => {
    const alert = canvas.getByRole('alert');
    await expect(alert).toHaveTextContent(i18n.t('errors:not_found'));
    await expect(alert).toHaveTextContent(notFound.requestId!);
  },
};

// A network failure: no response, so no reference line.
export const NetworkError: Story = {
  args: { error: new ApiError({ code: 'network_error' }) },
  play: async ({ canvas }) => {
    const alert = canvas.getByRole('alert');
    await expect(alert).toHaveTextContent(i18n.t('errors:network_error'));
    await expect(alert.querySelector('code')).toBeNull();
  },
};

// A query error with a "Try again" button.
export const WithRetry: Story = {
  args: {
    error: new ApiError({
      code: 'database_unavailable',
      status: 503,
      requestId: 'req-7d1c2b',
    }),
    onRetry: fn(),
  },
  play: async ({ args, canvas, userEvent }) => {
    await userEvent.click(
      canvas.getByRole('button', { name: i18n.t('feedback.retry') }),
    );
    await expect(args.onRetry).toHaveBeenCalledTimes(1);
  },
};
