import type { Meta, StoryObj } from '@storybook/react-vite';
import { expect } from 'storybook/test';
import { i18n } from '@/i18n';
import { CourseTemplateView } from './CourseTemplateView';
import { ALGEBRA } from './fixtures';

// The read-only course template (#64 G5, G7): what Finance Staff see.

const meta = {
  title: 'Courses/CourseTemplateView',
  component: CourseTemplateView,
  args: { template: ALGEBRA },
  render: (args) => (
    <main>
      <CourseTemplateView {...args} />
    </main>
  ),
} satisfies Meta<typeof CourseTemplateView>;

export default meta;
type Story = StoryObj<typeof meta>;

export const ReadOnly: Story = {
  play: async ({ canvas }) => {
    await expect(canvas.queryAllByRole('button')).toHaveLength(0);
    await expect(canvas.queryAllByRole('textbox')).toHaveLength(0);
    await expect(
      canvas.getByRole('heading', {
        level: 2,
        name: i18n.t('courses.templates.completion'),
      }),
    ).toBeVisible();
    // The assessment row and the required-assessments summary.
    await expect(canvas.getAllByText('Examen de funciones')).toHaveLength(2);
  },
};

export const WithoutAssessments: Story = {
  args: {
    template: {
      ...ALGEBRA,
      assessments: [],
      completion: { ...ALGEBRA.completion, required_assessment_ids: [] },
    },
  },
  play: async ({ canvas }) => {
    await expect(
      canvas.getByText(i18n.t('courses.templates.noAssessments')),
    ).toBeVisible();
  },
};
