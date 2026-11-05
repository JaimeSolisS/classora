import type { Meta, StoryObj } from '@storybook/react-vite';
import { expect } from 'storybook/test';
import { StoryQueryClient } from '@/test/StoryQueryClient';
import { StoryRouter } from '@/test/StoryRouter';
import { importKey, importRowsKey } from './api';
import { JOB_ID, rowPage, STAGED, STAGED_ROWS } from './fixtures';
import { ImportDetailPage } from './ImportDetailPage';

// An import's report (M8-05b C1, D1) whose file name is one long word: the
// heading wraps and a 320 px phone never scrolls sideways (QA #518 round 1).
// The job and its rows are seeded in the query cache: no request.

const LONG_NAME = `${'Alumnos_Primaria_Turno_Matutino_Grupo_A_'.repeat(2)}2026.csv`;
const JOB = { ...STAGED, file_name: LONG_NAME };

const meta = {
  title: 'Imports/ImportDetailPage',
  component: ImportDetailPage,
  render: () => (
    <StoryQueryClient
      data={[
        [importKey(JOB_ID), JOB],
        [
          importRowsKey(JOB_ID, JOB.version, { status: '', page: 1 }),
          rowPage(STAGED_ROWS),
        ],
      ]}
    >
      <StoryRouter
        path={`/imports/${JOB_ID}`}
        routes={[
          {
            path: '/imports/:importId',
            element: (
              <main>
                <ImportDetailPage />
              </main>
            ),
          },
        ]}
      />
    </StoryQueryClient>
  ),
} satisfies Meta<typeof ImportDetailPage>;

export default meta;
type Story = StoryObj<typeof meta>;

export const LongFileName: Story = {
  play: async ({ canvas }) => {
    await expect(
      canvas.getByRole('heading', { level: 1, name: LONG_NAME }),
    ).toBeVisible();
  },
};

/** A phone (320 px): the long name wraps; nothing scrolls sideways (D1). */
export const LongFileNameMobile: Story = {
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
  play: async ({ canvas, canvasElement }) => {
    const heading = canvas.getByRole('heading', { level: 1, name: LONG_NAME });
    await expect(heading).toBeVisible();
    const root = canvasElement.ownerDocument.documentElement;
    await expect(root.scrollWidth).toBeLessThanOrEqual(root.clientWidth);
    await expect(heading.scrollWidth).toBeLessThanOrEqual(heading.clientWidth);
  },
};
