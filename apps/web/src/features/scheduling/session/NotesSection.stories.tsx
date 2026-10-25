import type { Meta, StoryObj } from '@storybook/react-vite';
import { expect, fn, userEvent, within } from 'storybook/test';
import { i18n } from '@/i18n';
import {
  INTERNAL,
  SHARED,
  UNPUBLISHED,
  ZONE,
} from '@/features/tutor-portal/notes/fixtures';
import { NotesSection } from './NotesSection';

// The staff session page's Notes section (#79 I7): every note with its
// student, author, badge and text; "Stop sharing" on a shared note opens a
// dialog that requires a reason. At 390 px and 1280 px. No request: the
// unpublish is a stub. Axe runs in light and dark.

const VIEWPORTS = {
  phone390: {
    name: 'Phone (390 px)',
    styles: { width: '390px', height: '844px' },
    type: 'mobile',
  },
  desktop1280: {
    name: 'Desktop (1280 px)',
    styles: { width: '1280px', height: '800px' },
    type: 'desktop',
  },
} as const;

const STAFF_SHARED = { ...SHARED, is_own: false };

const meta = {
  title: 'Scheduling/NotesSection',
  component: NotesSection,
  args: {
    state: {
      status: 'ready',
      notes: [
        {
          ...INTERNAL,
          is_own: false,
          can_edit: false,
          can_publish: false,
          can_delete: false,
        },
        STAFF_SHARED,
        {
          ...UNPUBLISHED,
          is_own: false,
          can_edit: false,
          can_publish: false,
          can_delete: false,
        },
      ],
    },
    timeZone: ZONE,
    onStopSharing: fn(() => Promise.resolve()),
    onReload: fn(),
  },
  parameters: { viewport: { options: VIEWPORTS } },
  globals: { viewport: { value: 'phone390' } },
  render: (args) => (
    <main className="p-4">
      <NotesSection {...args} />
    </main>
  ),
} satisfies Meta<typeof NotesSection>;

export default meta;
type Story = StoryObj<typeof meta>;

export const Phone: Story = {};

export const Desktop: Story = {
  globals: { viewport: { value: 'desktop1280' } },
};

export const Empty: Story = {
  args: { state: { status: 'ready', notes: [] } },
};

export const StopSharingDialog: Story = {
  args: { initialStopping: STAFF_SHARED },
  play: async ({ canvasElement, args }) => {
    const dialog = within(canvasElement.ownerDocument.body).getByRole('dialog');
    await userEvent.click(
      within(dialog).getByRole('button', {
        name: i18n.t('notes.staff.dialog.confirm'),
      }),
    );
    await expect(
      within(dialog).getByText(i18n.t('notes.validation.reasonRequired')),
    ).toBeInTheDocument();
    await expect(args.onStopSharing).not.toHaveBeenCalled();
  },
};

export const StopSharingDialogDesktop: Story = {
  globals: { viewport: { value: 'desktop1280' } },
  args: { initialStopping: STAFF_SHARED },
};
