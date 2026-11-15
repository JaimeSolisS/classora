import type { Meta, StoryObj } from '@storybook/react-vite';
import { expect, fn } from 'storybook/test';
import { ApiError } from '@/app/api';
import { i18n } from '@/i18n';
import { AuditEventList } from './AuditEventList';
import { MASKED_RATE, MIXED_EVENTS } from './fixtures';

// The audit events (#120 C3-C7): mixed events, the expanded before/after
// view with a masked value, empty, an error and a 320 px phone. No request.

const meta = {
  title: 'Audit/AuditEventList',
  component: AuditEventList,
  args: {
    state: {
      status: 'ready',
      items: MIXED_EVENTS,
      hasMore: true,
      loadingMore: false,
      moreError: null,
      onLoadMore: fn(),
    },
    cursorInvalid: false,
    onRestart: fn(),
    onRecord: fn(),
    onActor: fn(),
  },
} satisfies Meta<typeof AuditEventList>;

export default meta;
type Story = StoryObj<typeof meta>;

export const MixedEvents: Story = {
  play: async ({ canvas }) => {
    // A header row plus one row per event.
    await expect(canvas.getAllByRole('row')).toHaveLength(
      MIXED_EVENTS.length + 1,
    );
    // Two events without an actor: a seed and a failed sign-in.
    await expect(canvas.getAllByText(i18n.t('audit.list.system'))).toHaveLength(
      2,
    );
    await expect(canvas.getByText('identity.login_failed')).toBeVisible();
    await expect(
      canvas.getByRole('button', { name: i18n.t('audit.list.loadMore') }),
    ).toBeVisible();
  },
};

/** The before/after view of a masked payroll create (C5, A2). */
export const MaskedChanges: Story = {
  args: {
    state: {
      status: 'ready',
      items: [MASKED_RATE],
      hasMore: false,
      loadingMore: false,
      moreError: null,
      onLoadMore: fn(),
    },
  },
  play: async ({ canvas, userEvent }) => {
    const toggle = canvas.getByRole('button', {
      name: /^View changes|^Ver cambios/,
    });
    await userEvent.click(toggle);
    await expect(toggle).toHaveAttribute('aria-expanded', 'true');
    await expect(
      canvas.getByText(i18n.t('audit.diff.maskedLabel')),
    ).toBeInTheDocument();
    await expect(canvas.queryByText('[masked]')).toBeNull();
  },
};

export const Empty: Story = {
  args: {
    state: {
      status: 'ready',
      items: [],
      hasMore: false,
      loadingMore: false,
      moreError: null,
      onLoadMore: fn(),
    },
  },
  play: async ({ canvas }) => {
    await expect(canvas.getByText(i18n.t('audit.list.empty'))).toBeVisible();
    await expect(canvas.queryByRole('table')).toBeNull();
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
        status: 500,
        code: 'internal_error',
        requestId: 'req-story',
      }),
      onRetry: fn(),
    },
  },
  play: async ({ canvas }) => {
    await expect(canvas.getByRole('alert')).toBeVisible();
  },
};

/** A phone (320 px): rows are cards, nothing scrolls sideways (C7). */
export const Mobile: Story = {
  parameters: {
    viewport: {
      options: {
        narrow320: {
          name: 'Narrow (320 px)',
          styles: { width: '320px', height: '640px' },
          type: 'mobile',
        },
      },
    },
  },
  globals: { viewport: { value: 'narrow320' } },
  play: async ({ canvas, canvasElement, userEvent }) => {
    const [toggle] = canvas.getAllByRole('button', {
      name: /^View changes|^Ver cambios/,
    });
    await userEvent.click(toggle!);
    const root = canvasElement.ownerDocument.documentElement;
    await expect(root.scrollWidth).toBeLessThanOrEqual(root.clientWidth);
  },
};
