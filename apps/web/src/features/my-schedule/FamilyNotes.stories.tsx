import type { Meta, StoryObj } from '@storybook/react-vite';
import { expect, fn, within } from 'storybook/test';
import { ApiError } from '@/app/api';
import { i18n } from '@/i18n';
import { FAMILY_NOTE, ZONE } from '@/features/tutor-portal/notes/fixtures';
import { FamilyNotesList } from './FamilyNotesSection';

// "Notes from the tutor" in a family's My schedule dialog (#79 I7): two
// shared notes and the inline failure with "Try again", at 320 px and
// 390 px. No request. Axe runs in light and dark.

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
  title: 'My schedule/FamilyNotes',
  component: FamilyNotesList,
  args: {
    timeZone: ZONE,
    notes: [
      FAMILY_NOTE,
      {
        ...FAMILY_NOTE,
        id: '00000000-0000-4000-8000-000000079a07',
        body: 'Para la próxima clase: traer la calculadora y el cuaderno de ejercicios.',
      },
    ],
  },
  parameters: { viewport: { options: VIEWPORTS } },
  globals: { viewport: { value: 'phone390' } },
  render: (args) => (
    <main className="p-4">
      <FamilyNotesList {...args} />
    </main>
  ),
} satisfies Meta<typeof FamilyNotesList>;

export default meta;
type Story = StoryObj<typeof meta>;

export const Phone: Story = {
  play: async ({ canvasElement }) => {
    await expect(
      within(canvasElement).getByRole('heading', {
        name: i18n.t('notes.family.title'),
      }),
    ).toBeInTheDocument();
  },
};

export const Narrow320: Story = {
  globals: { viewport: { value: 'narrow320' } },
  play: async ({ canvasElement }) => {
    const main = canvasElement.querySelector('main')!;
    await expect(main.scrollWidth).toBeLessThanOrEqual(main.clientWidth);
  },
};

export const Failed: Story = {
  args: {
    notes: [],
    error: new ApiError({ status: 500, code: 'internal_error' }),
    onRetry: fn(),
  },
};
