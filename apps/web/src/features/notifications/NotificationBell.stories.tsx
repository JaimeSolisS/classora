import type { Meta, StoryObj } from '@storybook/react-vite';
import { useState } from 'react';
import { expect, waitFor, within } from 'storybook/test';
import { ApiError } from '@/app/api';
import { i18n } from '@/i18n';
import type { NotificationItem } from './api';
import { NotificationBellView } from './NotificationBell';
import {
  NotificationPanel,
  type NotificationListState,
} from './NotificationPanel';

// #41 E6: the bell with 0, 3 and 120 unread, and its panel with read and
// unread items, empty, loading and error (fixed data: no request).

const ITEMS: NotificationItem[] = [
  {
    id: '00000000-0000-4000-8000-0000000000a3',
    notification_type: 'dev.demo',
    params: { label: 'Clase de álgebra reprogramada' },
    created_at: '2026-10-20T15:30:00Z',
    read_at: null,
  },
  {
    id: '00000000-0000-4000-8000-0000000000a2',
    notification_type: 'billing.unknown_type',
    params: {},
    created_at: '2026-10-20T12:05:00Z',
    read_at: null,
  },
  {
    id: '00000000-0000-4000-8000-0000000000a1',
    notification_type: 'dev.demo',
    params: { label: 'Bienvenida' },
    created_at: '2026-10-18T09:00:00Z',
    read_at: '2026-10-18T10:00:00Z',
  },
];

const ready = (items: NotificationItem[], hasMore = false) =>
  ({
    status: 'ready',
    items,
    hasMore,
    loadingMore: false,
    onLoadMore: () => {},
  }) satisfies NotificationListState;

function Bell({
  count,
  list,
  initiallyOpen = false,
}: {
  count: number;
  list: NotificationListState;
  initiallyOpen?: boolean;
}) {
  const [open, setOpen] = useState(initiallyOpen);
  return (
    <div className="flex justify-end p-4">
      <NotificationBellView
        count={count}
        open={open}
        onOpenChange={setOpen}
        headingId="notifications-heading"
      >
        <NotificationPanel
          headingId="notifications-heading"
          list={list}
          onActivate={() => {}}
          onMarkAll={() => {}}
        />
      </NotificationBellView>
    </div>
  );
}

const meta = {
  title: 'Features/Notifications/NotificationBell',
  component: Bell,
  args: { count: 3, list: ready(ITEMS) },
  parameters: { layout: 'fullscreen' },
} satisfies Meta<typeof Bell>;

export default meta;
type Story = StoryObj<typeof meta>;

const bellName = (count: number) =>
  count > 0
    ? i18n.t('notifications.bellUnread', {
        unread: count > 99 ? '99+' : String(count),
      })
    : i18n.t('notifications.bell');

export const NoUnread: Story = {
  args: { count: 0 },
  play: async ({ canvas }) => {
    const bell = canvas.getByRole('button', { name: bellName(0) });
    await expect(bell).toBeVisible();
    // WCAG 2.2 target size: at least 24 x 24 px.
    const box = bell.getBoundingClientRect();
    await expect(box.width).toBeGreaterThanOrEqual(24);
    await expect(box.height).toBeGreaterThanOrEqual(24);
  },
};

export const ThreeUnread: Story = {
  args: { count: 3 },
  play: async ({ canvas }) => {
    const bell = canvas.getByRole('button', { name: bellName(3) });
    await expect(bell.parentElement).toHaveTextContent('3');
  },
};

export const ManyUnread: Story = {
  args: { count: 120 },
  play: async ({ canvas }) => {
    const bell = canvas.getByRole('button', { name: bellName(120) });
    await expect(bell.parentElement).toHaveTextContent('99+');
  },
};

// Opened with the keyboard: read and unread items, "Load more"; Escape
// closes and returns focus to the bell.
export const PanelWithItems: Story = {
  args: { count: 2, list: ready(ITEMS, true) },
  play: async ({ canvas, canvasElement, userEvent }) => {
    const bell = canvas.getByRole('button', { name: bellName(2) });
    bell.focus();
    await userEvent.keyboard('{Enter}');

    const page = within(canvasElement.ownerDocument.body);
    const panel = await page.findByRole('dialog', {
      name: i18n.t('notifications.title'),
    });
    await expect(panel).toBeVisible();
    await expect(panel).toHaveTextContent(
      i18n.t('notifications.types.dev_demo', {
        label: 'Clase de álgebra reprogramada',
      }),
    );
    await expect(panel).toHaveTextContent(i18n.t('notifications.unknown'));
    await expect(panel).not.toHaveTextContent('billing');
    await expect(panel).toHaveTextContent(i18n.t('notifications.unread'));
    await expect(
      within(panel).getByRole('button', {
        name: i18n.t('notifications.loadMore'),
      }),
    ).toBeVisible();

    await userEvent.keyboard('{Escape}');
    await waitFor(() => expect(page.queryByRole('dialog')).toBeNull());
    await expect(bell).toHaveFocus();
  },
};

export const PanelOpen: Story = {
  args: { count: 2, list: ready(ITEMS), initiallyOpen: true },
};

export const PanelEmpty: Story = {
  args: { count: 0, list: ready([]), initiallyOpen: true },
  play: async ({ canvasElement }) => {
    const page = within(canvasElement.ownerDocument.body);
    await expect(
      await page.findByText(i18n.t('notifications.empty')),
    ).toBeVisible();
  },
};

export const PanelLoading: Story = {
  args: { count: 3, list: { status: 'loading' }, initiallyOpen: true },
};

export const PanelError: Story = {
  args: {
    count: 3,
    list: {
      status: 'error',
      error: new ApiError({
        code: 'internal_error',
        status: 500,
        requestId: 'req-notifications-0001',
      }),
      onRetry: () => {},
    },
    initiallyOpen: true,
  },
  play: async ({ canvasElement }) => {
    const page = within(canvasElement.ownerDocument.body);
    await expect(await page.findByRole('alert')).toHaveTextContent(
      'req-notifications-0001',
    );
  },
};
