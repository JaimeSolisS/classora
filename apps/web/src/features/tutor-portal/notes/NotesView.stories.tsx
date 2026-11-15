import type { Meta, StoryObj } from '@storybook/react-vite';
import { expect, fn, userEvent, within } from 'storybook/test';
import { ApiError } from '@/app/api';
import { i18n } from '@/i18n';
import {
  ANA,
  EARLIER,
  INTERNAL,
  READ_ONLY,
  SESSION,
  SHARED,
  UNPUBLISHED,
  ZONE,
} from './fixtures';
import { NotesView } from './NotesView';

// The tutor's notes page (#79 I7): empty, an internal note, a shared one,
// one unpublished by a reviewer with a reason, a read-only former student,
// the share confirmation and "Note changed elsewhere", at 320 px and
// 390 px. No request: the writes are stubs. Axe runs in light and dark (the
// storybook projects).

const VIEWPORTS = {
  narrow320: {
    name: 'Narrow (320 px)',
    styles: { width: '320px', height: '640px' },
    type: 'mobile',
  },
  phone390: {
    name: 'Phone (390 px)',
    styles: { width: '390px', height: '844px' },
    type: 'mobile',
  },
} as const;

const meta = {
  title: 'Tutor portal/NotesView',
  component: NotesView,
  args: {
    session: SESSION,
    timeZone: ZONE,
    studentId: ANA,
    onStudentChange: fn(),
    notes: { status: 'ready', items: [INTERNAL] },
    history: { state: { status: 'ready', items: [EARLIER] } },
    actions: {
      onCreate: fn(() => Promise.resolve()),
      onEdit: fn(() => Promise.resolve()),
      onShare: fn(() => Promise.resolve()),
      onStopSharing: fn(() => Promise.resolve()),
      onDelete: fn(() => Promise.resolve()),
      onReload: fn(),
    },
  },
  parameters: { viewport: { options: VIEWPORTS } },
  globals: { viewport: { value: 'phone390' } },
  render: (args) => (
    <main className="p-4">
      <h1 className="mb-4 text-2xl font-semibold">{i18n.t('notes.title')}</h1>
      <NotesView {...args} />
    </main>
  ),
} satisfies Meta<typeof NotesView>;

export default meta;
type Story = StoryObj<typeof meta>;

async function fitsTheWidth(canvasElement: HTMLElement) {
  const main = canvasElement.querySelector('main')!;
  await expect(main.scrollWidth).toBeLessThanOrEqual(main.clientWidth);
}

export const Empty: Story = {
  args: {
    notes: { status: 'ready', items: [] },
    history: { state: { status: 'ready', items: [] } },
  },
  play: async ({ canvasElement, args }) => {
    const canvas = within(canvasElement);
    await userEvent.click(
      canvas.getByRole('button', { name: i18n.t('notes.composer.save') }),
    );
    await expect(
      canvas.getByText(i18n.t('notes.validation.bodyRequired')),
    ).toBeInTheDocument();
    await expect(args.actions.onCreate).not.toHaveBeenCalled();
    await fitsTheWidth(canvasElement);
  },
};

export const Internal: Story = {
  play: async ({ canvasElement }) => {
    await expect(
      within(canvasElement).getAllByText(i18n.t('notes.badges.internal')),
    ).toHaveLength(2);
    await fitsTheWidth(canvasElement);
  },
};

export const InternalNarrow320: Story = {
  globals: { viewport: { value: 'narrow320' } },
  args: { notes: { status: 'ready', items: [INTERNAL, SHARED] } },
  play: async ({ canvasElement }) => fitsTheWidth(canvasElement),
};

export const Shared: Story = {
  args: { notes: { status: 'ready', items: [SHARED] } },
  play: async ({ canvasElement }) => fitsTheWidth(canvasElement),
};

export const UnpublishedWithReason: Story = {
  args: { notes: { status: 'ready', items: [UNPUBLISHED] } },
  play: async ({ canvasElement }) => {
    await expect(
      within(canvasElement).getByText(
        i18n.t('notes.unpublishReason', {
          reason: UNPUBLISHED.unpublish_reason,
        }),
      ),
    ).toBeInTheDocument();
  },
};

export const ReadOnlyFormer: Story = {
  globals: { viewport: { value: 'narrow320' } },
  args: { notes: { status: 'ready', items: [READ_ONLY] } },
  play: async ({ canvasElement }) => {
    await expect(
      within(canvasElement).queryByRole('button', {
        name: i18n.t('notes.actions.edit'),
      }),
    ).toBeNull();
    await fitsTheWidth(canvasElement);
  },
};

export const ShareDialog: Story = {
  args: { initialSharing: INTERNAL },
  play: async ({ canvasElement }) => {
    const dialog = within(canvasElement.ownerDocument.body).getByRole('dialog');
    await expect(within(dialog).getByText(INTERNAL.body)).toBeInTheDocument();
  },
};

export const ShareDialogNarrow320: Story = {
  globals: { viewport: { value: 'narrow320' } },
  args: { initialSharing: INTERNAL },
};

export const ChangedElsewhere: Story = {
  args: {
    notes: { status: 'ready', items: [SHARED] },
    initialFailure: new ApiError({ status: 409, code: 'version_conflict' }),
  },
  play: async ({ canvasElement }) => {
    await expect(
      within(canvasElement).getByText(i18n.t('notes.changed')),
    ).toBeInTheDocument();
  },
};

export const Loading: Story = {
  args: {
    notes: { status: 'loading' },
    history: { state: { status: 'loading' } },
  },
};
