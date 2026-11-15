import type { Meta, StoryObj } from '@storybook/react-vite';
import { expect, fn } from 'storybook/test';
import { ApiError } from '@/app/api';
import { i18n } from '@/i18n';
import { StoryRouter } from '@/test/StoryRouter';
import { FamilyForm } from './FamilyForm';
import { NUNEZ } from './fixtures';
import { EMPTY_FAMILY, valuesOf } from './rules';

// The family form (#172 H2, I2): an empty create form, and an edit that
// answers version_conflict with "Reload". No request.

const meta = {
  title: 'Families/FamilyForm',
  component: FamilyForm,
  args: {
    defaultValues: EMPTY_FAMILY,
    cancelTo: '/families',
    labelledBy: 'family-form-title',
    onSubmit: fn(() => Promise.resolve()),
    onReload: fn(() => Promise.resolve()),
  },
  render: (args) => (
    <StoryRouter
      routes={[
        {
          path: '*',
          element: (
            <>
              <h1 id="family-form-title">{i18n.t('families.add')}</h1>
              <FamilyForm {...args} />
            </>
          ),
        },
      ]}
    />
  ),
} satisfies Meta<typeof FamilyForm>;

export default meta;
type Story = StoryObj<typeof meta>;

export const Create: Story = {
  play: async ({ canvas }) => {
    await expect(
      canvas.getByLabelText(i18n.t('families.fields.name')),
    ).toBeVisible();
  },
};

export const EditConflict: Story = {
  args: {
    defaultValues: valuesOf(NUNEZ),
    onSubmit: fn(() =>
      Promise.reject(new ApiError({ code: 'version_conflict', status: 409 })),
    ),
  },
  play: async ({ canvas, userEvent }) => {
    await userEvent.click(
      canvas.getByRole('button', { name: i18n.t('families.save') }),
    );
    await expect(
      await canvas.findByRole('button', {
        name: i18n.t('families.conflict.reload'),
      }),
    ).toBeVisible();
  },
};
