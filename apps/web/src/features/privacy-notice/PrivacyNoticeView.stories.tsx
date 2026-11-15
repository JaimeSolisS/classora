import type { Meta, StoryObj } from '@storybook/react-vite';
import { expect, fn } from 'storybook/test';
import { ApiError } from '@/app/api';
import { i18n } from '@/i18n';
import { NOTICE_V1, NOTICE_V1_ACCEPTED, NOTICE_V2 } from './fixtures';
import { PrivacyNoticeView } from './PrivacyNoticeView';

// The privacy-notice page (#174 G5): to accept, accepted (reread), the 409
// message with the newer version, loading and a load error. No request.

const meta = {
  title: 'PrivacyNotice/PrivacyNoticeView',
  component: PrivacyNoticeView,
  args: {
    state: { status: 'ready', notice: NOTICE_V1 },
    accepting: false,
    changed: false,
    acceptError: null,
    onAccept: fn(),
  },
} satisfies Meta<typeof PrivacyNoticeView>;

export default meta;
type Story = StoryObj<typeof meta>;

export const ToAccept: Story = {
  play: async ({ canvas, args, userEvent }) => {
    await expect(
      canvas.getByRole('heading', {
        level: 1,
        name: i18n.t('privacyNotice.title'),
      }),
    ).toHaveFocus();
    const region = canvas.getByRole('region', {
      name: i18n.t('privacyNotice.textLabel'),
    });
    await expect(region).toHaveAttribute('tabindex', '0');
    await userEvent.click(
      canvas.getByRole('button', { name: i18n.t('privacyNotice.accept') }),
    );
    await expect(args.onAccept).toHaveBeenCalledWith(1);
  },
};

export const Accepted: Story = {
  args: { state: { status: 'ready', notice: NOTICE_V1_ACCEPTED } },
  play: async ({ canvas }) => {
    await expect(
      canvas.queryByRole('button', { name: i18n.t('privacyNotice.accept') }),
    ).toBeNull();
  },
};

export const Changed: Story = {
  args: { state: { status: 'ready', notice: NOTICE_V2 }, changed: true },
  play: async ({ canvas }) => {
    await expect(canvas.getByRole('status')).toHaveTextContent(
      i18n.t('privacyNotice.changed'),
    );
  },
};

export const Accepting: Story = {
  args: { accepting: true },
  play: async ({ canvas }) => {
    await expect(canvas.getByRole('button')).toBeDisabled();
  },
};

export const AcceptError: Story = {
  args: {
    acceptError: new ApiError({
      code: 'internal_error',
      status: 500,
      requestId: 'req-story-1',
    }),
  },
  play: async ({ canvas }) => {
    await expect(canvas.getByRole('alert')).toBeVisible();
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
        code: 'internal_error',
        status: 500,
        requestId: 'req-story-2',
      }),
      onRetry: fn(),
    },
  },
  play: async ({ canvas }) => {
    await expect(canvas.getByRole('alert')).toBeVisible();
  },
};
