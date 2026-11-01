import type { Meta, StoryObj } from '@storybook/react-vite';
import { expect, fn } from 'storybook/test';
import { i18n } from '@/i18n';
import { StoryRouter } from '@/test/StoryRouter';
import { EMPTY_PAGE, TUTORS_PAGE } from './fixtures';
import { TutorPayList } from './TutorPayList';

// The payroll tutor list (#425 H2): the table, the cards at 320 px, loading,
// empty and error. No request. axe runs on each story in both themes.

const meta = {
  title: 'Payroll/TutorPayList',
  component: TutorPayList,
  args: {
    state: { status: 'ready', page: TUTORS_PAGE },
    labelledBy: 'tutors-title',
    onPage: fn(),
  },
  render: (args) => (
    <StoryRouter
      path="/payroll/tutors"
      routes={[
        {
          path: '*',
          element: (
            <main className="p-4">
              <h1 id="tutors-title">{i18n.t('payroll.list.title')}</h1>
              <TutorPayList {...args} />
            </main>
          ),
        },
      ]}
    />
  ),
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
} satisfies Meta<typeof TutorPayList>;

export default meta;
type Story = StoryObj<typeof meta>;

export const Ready: Story = {
  play: async ({ canvas }) => {
    await expect(
      canvas.getByRole('table', { name: i18n.t('payroll.list.title') }),
    ).toBeVisible();
    await expect(
      canvas.getByRole('link', { name: 'Ana Tutora' }),
    ).toBeVisible();
  },
};

export const Cards: Story = {
  globals: { viewport: { value: 'narrow320' } },
  play: async ({ canvas, canvasElement }) => {
    await expect(canvas.queryByRole('table')).toBeNull();
    await expect(
      canvas.getByRole('link', { name: 'Ana Tutora' }),
    ).toBeVisible();
    const main = canvasElement.querySelector('main')!;
    await expect(main.scrollWidth).toBeLessThanOrEqual(main.clientWidth);
  },
};

export const Empty: Story = {
  args: { state: { status: 'ready', page: EMPTY_PAGE } },
  play: async ({ canvas }) => {
    await expect(canvas.getByText(i18n.t('payroll.list.empty'))).toBeVisible();
  },
};

export const Loading: Story = {
  args: { state: { status: 'loading' } },
};

export const Failed: Story = {
  args: {
    state: { status: 'error', error: new Error('boom'), onRetry: fn() },
  },
};
