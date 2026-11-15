import type { Meta, StoryObj } from '@storybook/react-vite';
import { expect, fn } from 'storybook/test';
import { i18n } from '@/i18n';
import { ExportHistory } from './ExportHistory';
import {
  EXPIRED,
  FAILED,
  jobPage,
  NOW,
  QUEUED,
  READY,
  READY_PAST_EXPIRY,
  READY_XLSX,
} from './fixtures';

// The export history (M8-07b C1, E2): ready (a download link and the time
// left), queued, failed (the reason) and expired (no link), at a pinned
// time; empty; and a 320 px phone. No request: the links aren't followed.

const meta = {
  title: 'Exports/ExportHistory',
  component: ExportHistory,
  args: {
    state: {
      status: 'ready',
      page: jobPage([
        QUEUED,
        READY,
        READY_XLSX,
        FAILED,
        READY_PAST_EXPIRY,
        EXPIRED,
      ]),
    },
    labelledBy: 'history-title',
    onPage: fn(),
    now: NOW,
  },
  render: (args) => (
    <>
      <h2 id="history-title">{i18n.t('exports.history.title')}</h2>
      <ExportHistory {...args} />
    </>
  ),
} satisfies Meta<typeof ExportHistory>;

export default meta;
type Story = StoryObj<typeof meta>;

export const AllStatuses: Story = {
  play: async ({ canvas, canvasElement }) => {
    await expect(canvas.getAllByRole('row')).toHaveLength(7);
    const row = (id: string) =>
      canvasElement.querySelector(`tr[data-export-id="${id}"]`)!;
    // Ready: a download link named after the file, and the time left.
    const link = row(READY.id).querySelector('a')!;
    await expect(link.getAttribute('download')).toBe(READY.file_name);
    await expect(row(READY.id).textContent).toContain(
      i18n.t('exports.history.expiresInHours', { hours: 5 }),
    );
    await expect(row(READY_XLSX.id).textContent).toContain(
      i18n.t('exports.history.expiresInMinutes', { minutes: 20 }),
    );
    // Queued, failed, past expiry and expired: no link.
    for (const job of [QUEUED, FAILED, READY_PAST_EXPIRY, EXPIRED]) {
      await expect(row(job.id).querySelector('a')).toBeNull();
    }
    await expect(row(FAILED.id).textContent).toContain(
      i18n.t('exports.history.failure.row_limit_exceeded'),
    );
    await expect(row(EXPIRED.id).textContent).toContain(
      i18n.t('exports.history.expired'),
    );
  },
};

export const Queued: Story = {
  args: { state: { status: 'ready', page: jobPage([QUEUED]) } },
  play: async ({ canvas }) => {
    await expect(
      canvas.getByText(i18n.t('exports.status.queued')),
    ).toBeVisible();
  },
};

export const Empty: Story = {
  args: { state: { status: 'ready', page: jobPage([]) } },
  play: async ({ canvas }) => {
    await expect(
      canvas.getByText(i18n.t('exports.history.empty')),
    ).toBeVisible();
  },
};

/** A phone (320 px): the table scrolls inside its own container. */
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
  play: async ({ canvasElement }) => {
    const root = canvasElement.ownerDocument.documentElement;
    await expect(root.scrollWidth).toBeLessThanOrEqual(root.clientWidth);
  },
};
