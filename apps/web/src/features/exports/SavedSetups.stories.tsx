import type { Meta, StoryObj } from '@storybook/react-vite';
import { expect, fn } from 'storybook/test';
import { i18n } from '@/i18n';
import { SETUP_NOT_RUNNABLE, SETUP_OWN, SETUP_SHARED } from './fixtures';
import { SavedSetups } from './SavedSetups';

// The saved setups (M8-08 D1, D2): the caller's own (shared with a role),
// another user's shared with the caller (no Edit or Delete), one whose
// data the caller may no longer export (only Delete); empty; and a 320 px
// phone. No request: the actions are spies.

const meta = {
  title: 'Exports/SavedSetups',
  component: SavedSetups,
  args: {
    state: {
      status: 'ready',
      items: [SETUP_SHARED, SETUP_OWN, SETUP_NOT_RUNNABLE],
    },
    runningId: null,
    notice: null,
    failure: null,
    onRun: fn(),
    onLoad: fn(),
    onEdit: fn(async () => {}),
    onDelete: fn(async () => {}),
    onStale: fn(),
  },
} satisfies Meta<typeof SavedSetups>;

export default meta;
type Story = StoryObj<typeof meta>;

export const OwnSharedAndNotRunnable: Story = {
  play: async ({ canvas, canvasElement, args }) => {
    const row = (id: string) =>
      canvasElement.querySelector(`li[data-setup-id="${id}"]`)!;
    await expect(row(SETUP_SHARED.id).textContent).toContain(
      i18n.t('exports.setups.by', { name: 'Carla Coordinator' }),
    );
    await expect(row(SETUP_NOT_RUNNABLE.id).textContent).toContain(
      i18n.t('exports.setups.noAccess'),
    );
    await expect(row(SETUP_SHARED.id).querySelectorAll('button')).toHaveLength(
      2,
    );
    const run = canvas.getByRole('button', {
      name: i18n.t('exports.setups.runFor', { name: SETUP_OWN.name }),
    });
    run.click();
    await expect(args.onRun).toHaveBeenCalledWith(SETUP_OWN);
  },
};

export const Running: Story = {
  args: { runningId: SETUP_OWN.id },
  play: async ({ canvas }) => {
    await expect(
      canvas.getByText(i18n.t('exports.setups.running')),
    ).toBeVisible();
  },
};

export const Empty: Story = {
  args: { state: { status: 'ready', items: [] } },
  play: async ({ canvas }) => {
    await expect(
      canvas.getByText(i18n.t('exports.setups.empty')),
    ).toBeVisible();
  },
};

/** A phone (320 px): the actions wrap under each setup. */
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
