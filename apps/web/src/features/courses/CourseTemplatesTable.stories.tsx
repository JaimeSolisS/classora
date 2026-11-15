import type { Meta, StoryObj } from '@storybook/react-vite';
import { expect, fn } from 'storybook/test';
import { ApiError } from '@/app/api';
import { i18n } from '@/i18n';
import { StoryRouter } from '@/test/StoryRouter';
import { CourseTemplatesTable } from './CourseTemplatesTable';
import { ALGEBRA, pageOf, TEMPLATES } from './fixtures';

// The course templates list (#64 G2, G7): active and inactive rows, empty,
// loading and error. No request.

const meta = {
  title: 'Courses/CourseTemplatesTable',
  component: CourseTemplatesTable,
  args: {
    state: { status: 'ready', page: pageOf(TEMPLATES) },
    labelledBy: 'templates-title',
    onPage: fn(),
  },
  render: (args) => (
    <StoryRouter
      routes={[
        {
          path: '*',
          element: (
            <>
              <h1 id="templates-title">{i18n.t('courses.templates.title')}</h1>
              <CourseTemplatesTable {...args} />
            </>
          ),
        },
      ]}
    />
  ),
} satisfies Meta<typeof CourseTemplatesTable>;

export default meta;
type Story = StoryObj<typeof meta>;

export const Templates: Story = {
  play: async ({ canvas }) => {
    await expect(
      canvas.getByRole('table', { name: i18n.t('courses.templates.title') }),
    ).toBeVisible();
    await expect(canvas.getAllByRole('row')).toHaveLength(TEMPLATES.length + 1);
    await expect(
      canvas.getByRole('link', { name: ALGEBRA.name }),
    ).toHaveAttribute('href', `/courses/templates/${ALGEBRA.id}`);
    await expect(
      canvas.getByText(i18n.t('catalog.status.inactive')),
    ).toBeVisible();
  },
};

export const Empty: Story = {
  args: { state: { status: 'ready', page: pageOf([]) } },
  play: async ({ canvas }) => {
    await expect(
      canvas.getByText(i18n.t('courses.templates.empty')),
    ).toBeVisible();
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
