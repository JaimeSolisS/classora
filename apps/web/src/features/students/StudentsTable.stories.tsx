import type { Meta, StoryObj } from '@storybook/react-vite';
import { expect, fn } from 'storybook/test';
import { ApiError } from '@/app/api';
import { i18n } from '@/i18n';
import { StoryRouter } from '@/test/StoryRouter';
import { JOSE_ID, LIST_ITEMS, studentPage } from './fixtures';
import { StudentsTable } from './StudentsTable';

// The student list (#33 I3, K4): rows with mixed status and minor/adult, an
// empty filter result, no results for a search, loading and error. No
// request.

const meta = {
  title: 'Students/StudentsTable',
  component: StudentsTable,
  args: {
    state: { status: 'ready', page: studentPage(LIST_ITEMS, 30) },
    labelledBy: 'students-title',
    searching: false,
    onPage: fn(),
  },
  render: (args) => (
    <StoryRouter
      routes={[
        {
          path: '*',
          element: (
            <>
              <h1 id="students-title">{i18n.t('students.title')}</h1>
              <StudentsTable {...args} />
            </>
          ),
        },
      ]}
    />
  ),
} satisfies Meta<typeof StudentsTable>;

export default meta;
type Story = StoryObj<typeof meta>;

export const Rows: Story = {
  play: async ({ canvas, userEvent, args }) => {
    await expect(
      canvas.getByRole('table', { name: i18n.t('students.title') }),
    ).toBeVisible();
    await expect(canvas.getAllByRole('row')).toHaveLength(
      LIST_ITEMS.length + 1,
    );
    await expect(
      canvas.getByRole('link', { name: 'Ñúñez Pérez, José' }),
    ).toHaveAttribute('href', `/students/${JOSE_ID}`);
    await expect(
      canvas.getByText(i18n.t('students.badge.minor')),
    ).toBeVisible();
    await expect(
      canvas.getByText(i18n.t('students.status.inactive')),
    ).toBeVisible();
    await expect(
      canvas.getByText(`Posgrado ${i18n.t('students.fields.levelInactive')}`),
    ).toBeVisible();
    await userEvent.click(
      canvas.getByRole('button', { name: i18n.t('users.list.next') }),
    );
    await expect(args.onPage).toHaveBeenCalledWith(2);
  },
};

export const Empty: Story = {
  args: { state: { status: 'ready', page: studentPage([]) } },
  play: async ({ canvas }) => {
    await expect(canvas.getByText(i18n.t('students.list.empty'))).toBeVisible();
    await expect(canvas.queryByRole('table')).toBeNull();
  },
};

export const NoResults: Story = {
  args: { state: { status: 'ready', page: studentPage([]) }, searching: true },
  play: async ({ canvas }) => {
    await expect(
      canvas.getByText(i18n.t('students.list.noResults')),
    ).toBeVisible();
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
