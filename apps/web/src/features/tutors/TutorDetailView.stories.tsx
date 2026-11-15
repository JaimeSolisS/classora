import type { Meta, StoryObj } from '@storybook/react-vite';
import { expect, fn, within } from 'storybook/test';
import { ApiError } from '@/app/api';
import { i18n } from '@/i18n';
import { StoryRouter } from '@/test/StoryRouter';
import {
  NEW_TUTOR,
  TUTOR_AS_ADMIN,
  TUTOR_AS_SELF,
  TUTOR_READ_ONLY,
} from './fixtures';
import { TutorDetailView } from './TutorDetailPage';

// The tutor profile page (#43 E3, E5): as an Administrator (every field), as
// the tutor themselves (no name field), read only (an Academic
// Coordinator), a tutor without a stored profile, and the form with a
// server error (version_conflict with Reload). No request: every call is a
// stub.

const meta = {
  title: 'Tutors/TutorDetailView',
  component: TutorDetailView,
  args: {
    tutor: TUTOR_AS_ADMIN,
    onSave: fn(() => Promise.resolve()),
    onReload: fn(() => Promise.resolve()),
  },
  render: (args) => (
    <StoryRouter
      routes={[{ path: '*', element: <TutorDetailView {...args} /> }]}
    />
  ),
} satisfies Meta<typeof TutorDetailView>;

export default meta;
type Story = StoryObj<typeof meta>;

export const AsAdministrator: Story = {
  play: async ({ canvas, userEvent }) => {
    await expect(
      canvas.getByRole('heading', {
        level: 1,
        name: TUTOR_AS_ADMIN.display_name,
      }),
    ).toBeVisible();
    await expect(canvas.getByText('lucia@example.com')).toBeVisible();
    await userEvent.click(
      canvas.getByRole('button', { name: i18n.t('tutors.profile.edit') }),
    );
    await expect(
      canvas.getByLabelText(i18n.t('tutors.profile.displayName')),
    ).toBeVisible();
    await expect(
      canvas.getByLabelText(i18n.t('tutors.form.experience')),
    ).toBeVisible();
  },
};

export const AsTheTutor: Story = {
  args: { tutor: TUTOR_AS_SELF, initiallyEditing: true },
  play: async ({ canvas }) => {
    await expect(
      canvas.queryByLabelText(i18n.t('tutors.profile.displayName')),
    ).toBeNull();
    await expect(
      canvas.getByLabelText(i18n.t('tutors.form.phone')),
    ).toHaveValue(TUTOR_AS_SELF.phone);
  },
};

export const ReadOnly: Story = {
  args: { tutor: TUTOR_READ_ONLY },
  play: async ({ canvas }) => {
    await expect(
      canvas.queryByRole('button', { name: i18n.t('tutors.profile.edit') }),
    ).toBeNull();
    await expect(canvas.getByText(/Eight years teaching/)).toBeVisible();
  },
};

export const WithoutProfile: Story = {
  args: { tutor: NEW_TUTOR },
  play: async ({ canvas }) => {
    await expect(
      canvas.getAllByText(i18n.t('tutors.profile.notSet')).length,
    ).toBeGreaterThanOrEqual(2);
  },
};

export const ServerError: Story = {
  args: {
    initiallyEditing: true,
    onSave: fn(() =>
      Promise.reject(
        new ApiError({
          code: 'version_conflict',
          status: 409,
          requestId: 'req-story',
        }),
      ),
    ),
  },
  play: async ({ canvas, userEvent, args }) => {
    await userEvent.clear(canvas.getByLabelText(i18n.t('tutors.form.phone')));
    await userEvent.type(
      canvas.getByLabelText(i18n.t('tutors.form.phone')),
      '55 0000 1111',
    );
    await userEvent.click(
      canvas.getByRole('button', { name: i18n.t('tutors.form.save') }),
    );
    const alert = await canvas.findByRole('alert');
    await expect(alert).toHaveTextContent(i18n.t('errors:version_conflict'));
    await userEvent.click(
      within(alert).getByRole('button', { name: i18n.t('tutors.form.reload') }),
    );
    await expect(args.onReload).toHaveBeenCalled();
  },
};
