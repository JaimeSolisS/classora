import type { Meta, StoryObj } from '@storybook/react-vite';
import { expect, fn } from 'storybook/test';
import { ApiError } from '@/app/api';
import { i18n } from '@/i18n';
import { StoryRouter } from '@/test/StoryRouter';
import { TUTOR_ITEM, TUTOR_ITEMS, tutorPage } from './fixtures';
import { TutorsTable } from './TutorsTable';

// The tutor list (#43 E2, E5): rows, a tutor's own one-row list, empty,
// loading and error. No request.

const meta = {
  title: 'Tutors/TutorsTable',
  component: TutorsTable,
  args: {
    state: { status: 'ready', page: tutorPage(TUTOR_ITEMS, 30) },
    labelledBy: 'tutors-title',
    onPage: fn(),
  },
  render: (args) => (
    <StoryRouter
      routes={[
        {
          path: '*',
          element: (
            <>
              <h1 id="tutors-title">{i18n.t('tutors.title')}</h1>
              <TutorsTable {...args} />
            </>
          ),
        },
      ]}
    />
  ),
} satisfies Meta<typeof TutorsTable>;

export default meta;
type Story = StoryObj<typeof meta>;

export const Rows: Story = {
  play: async ({ canvas, userEvent, args }) => {
    await expect(
      canvas.getByRole('table', { name: i18n.t('tutors.title') }),
    ).toBeVisible();
    await expect(canvas.getAllByRole('row')).toHaveLength(4);
    await expect(
      canvas.getByRole('link', { name: TUTOR_ITEM.display_name }),
    ).toHaveAttribute('href', `/tutors/${TUTOR_ITEM.user_id}`);
    await expect(
      canvas.getByText(i18n.t('userStates.suspended')),
    ).toBeVisible();
    await expect(
      canvas.getByRole('button', { name: i18n.t('tutors.list.previous') }),
    ).toBeDisabled();
    await userEvent.click(
      canvas.getByRole('button', { name: i18n.t('tutors.list.next') }),
    );
    await expect(args.onPage).toHaveBeenCalledWith(2);
  },
};

export const OwnRowOnly: Story = {
  args: { state: { status: 'ready', page: tutorPage([TUTOR_ITEM]) } },
  play: async ({ canvas }) => {
    await expect(canvas.getAllByRole('row')).toHaveLength(2);
    await expect(
      canvas.getByRole('button', { name: i18n.t('tutors.list.next') }),
    ).toBeDisabled();
  },
};

export const Empty: Story = {
  args: { state: { status: 'ready', page: tutorPage([]) } },
  play: async ({ canvas }) => {
    await expect(canvas.getByText(i18n.t('tutors.list.empty'))).toBeVisible();
    await expect(canvas.queryByRole('table')).toBeNull();
  },
};

export const Loading: Story = {
  args: { state: { status: 'loading' } },
  play: async ({ canvasElement }) => {
    await expect(
      canvasElement.querySelector('[aria-busy="true"]'),
    ).not.toBeNull();
  },
};

export const LoadError: Story = {
  args: {
    state: {
      status: 'error',
      error: new ApiError({
        code: 'internal_error',
        status: 500,
        requestId: 'req-story',
      }),
      onRetry: fn(),
    },
  },
  play: async ({ canvas }) => {
    await expect(canvas.getByRole('alert')).toHaveTextContent(
      i18n.t('errors:internal_error'),
    );
  },
};
