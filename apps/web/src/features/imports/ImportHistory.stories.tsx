import type { Meta, StoryObj } from '@storybook/react-vite';
import { expect, fn } from 'storybook/test';
import { i18n } from '@/i18n';
import { StoryRouter } from '@/test/StoryRouter';
import {
  COMMITTED,
  DISCARDED,
  FAILED,
  jobPage,
  TUTORS_STAGED,
} from './fixtures';
import { ImportHistory } from './ImportHistory';

// The import history (M8-05b B4, D1): mixed statuses, empty and a 320 px
// phone. No request.

const meta = {
  title: 'Imports/ImportHistory',
  component: ImportHistory,
  args: {
    state: {
      status: 'ready',
      page: jobPage([TUTORS_STAGED, COMMITTED, FAILED, DISCARDED]),
    },
    labelledBy: 'history-title',
    filtered: false,
    onPage: fn(),
  },
  render: (args) => (
    <StoryRouter
      routes={[
        {
          path: '*',
          element: (
            <>
              <h2 id="history-title">{i18n.t('imports.history.title')}</h2>
              <ImportHistory {...args} />
            </>
          ),
        },
      ]}
    />
  ),
} satisfies Meta<typeof ImportHistory>;

export default meta;
type Story = StoryObj<typeof meta>;

export const MixedStatuses: Story = {
  play: async ({ canvas }) => {
    await expect(canvas.getAllByRole('row')).toHaveLength(5);
    await expect(
      canvas.getByText(i18n.t('imports.status.failed')),
    ).toBeVisible();
  },
};

export const Empty: Story = {
  args: { state: { status: 'ready', page: jobPage([]) } },
  play: async ({ canvas }) => {
    await expect(
      canvas.getByText(i18n.t('imports.history.empty')),
    ).toBeVisible();
  },
};

/** A phone (320 px): the table scrolls inside its container and a long
 * file name wraps (D1). */
export const Mobile: Story = {
  args: {
    state: {
      status: 'ready',
      page: jobPage([
        {
          ...TUTORS_STAGED,
          file_name: `${'Tutores_Turno_Vespertino_'.repeat(3)}2026.csv`,
        },
        COMMITTED,
      ]),
    },
  },
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
