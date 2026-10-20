import type { Meta, StoryObj } from '@storybook/react-vite';
import { expect, fn, screen, waitFor, within } from 'storybook/test';
import { ApiError } from '@/app/api';
import { i18n } from '@/i18n';
import { StoryQueryClient } from '@/test/StoryQueryClient';
import { StoryRouter } from '@/test/StoryRouter';
import {
  LEVEL_PREPARATORIA,
  LEVEL_SECUNDARIA,
  QUALIFICATIONS,
  SUBJECT_ALGEBRA,
  SUBJECT_CALCULO,
  SUBJECT_FISICA,
  TEACHING_SUBJECTS,
} from './fixtures';
import { QualificationsSection } from './QualificationsSection';

// The tutor profile's Qualifications section (#44 F4): every status as a
// word, the teaching subjects, the empty state, and the request dialog
// answering 409 qualification_exists. No request: every call is a stub.

const meta = {
  title: 'Tutors/QualificationsSection',
  component: QualificationsSection,
  args: {
    state: { status: 'ready', items: QUALIFICATIONS },
    teachingSubjects: TEACHING_SUBJECTS,
    canRequest: true,
    onRequest: fn(() => Promise.resolve()),
    onCancel: fn(() => Promise.resolve()),
    loadLevels: fn(() =>
      Promise.resolve([LEVEL_SECUNDARIA, LEVEL_PREPARATORIA]),
    ),
    loadSubjects: fn((levelId: string) =>
      Promise.resolve(
        levelId === LEVEL_SECUNDARIA.id
          ? [SUBJECT_ALGEBRA, SUBJECT_FISICA]
          : [SUBJECT_CALCULO],
      ),
    ),
  },
  render: (args) => (
    <StoryQueryClient>
      <StoryRouter
        routes={[
          {
            path: '*',
            element: (
              <main>
                <QualificationsSection {...args} />
              </main>
            ),
          },
        ]}
      />
    </StoryQueryClient>
  ),
} satisfies Meta<typeof QualificationsSection>;

export default meta;
type Story = StoryObj<typeof meta>;

export const MixedStatuses: Story = {
  play: async ({ canvas }) => {
    await expect(
      canvas.getByRole('heading', {
        level: 2,
        name: i18n.t('tutors.qualifications.title'),
      }),
    ).toBeVisible();
    for (const status of [
      'approved',
      'pending',
      'rejected',
      'cancelled',
    ] as const) {
      await expect(
        canvas.getByText(i18n.t(`tutors.qualifications.statuses.${status}`)),
      ).toBeVisible();
    }
    await expect(
      canvas.getByText('Please add a certificate for calculus first.'),
    ).toBeVisible();
    // Only the pending row can be cancelled.
    await expect(
      canvas.getAllByRole('button', {
        name: /^(Cancel request|Cancelar solicitud) /,
      }),
    ).toHaveLength(1);
  },
};

export const ReadOnly: Story = {
  args: { canRequest: false },
  play: async ({ canvas }) => {
    await expect(
      canvas.queryByRole('button', {
        name: i18n.t('tutors.qualifications.request'),
      }),
    ).toBeNull();
  },
};

export const Empty: Story = {
  args: { state: { status: 'ready', items: [] }, teachingSubjects: [] },
  play: async ({ canvas }) => {
    await expect(
      canvas.getByText(i18n.t('tutors.qualifications.empty')),
    ).toBeVisible();
    await expect(
      canvas.getByText(i18n.t('tutors.qualifications.teachingEmpty')),
    ).toBeVisible();
  },
};

export const RequestDialogExists: Story = {
  args: {
    onRequest: fn(() =>
      Promise.reject(
        new ApiError({ code: 'qualification_exists', status: 409 }),
      ),
    ),
  },
  play: async ({ canvas, userEvent, args }) => {
    await userEvent.click(
      canvas.getByRole('button', {
        name: i18n.t('tutors.qualifications.request'),
      }),
    );
    const dialog = within(await screen.findByRole('dialog'));
    const levelSelect = dialog.getByRole('combobox', {
      name: i18n.t('tutors.qualifications.levelLabel'),
    });
    await dialog.findByRole('option', { name: LEVEL_SECUNDARIA.name });
    await userEvent.selectOptions(levelSelect, LEVEL_SECUNDARIA.id);
    await waitFor(() => expect(args.loadSubjects).toHaveBeenCalled());
    await dialog.findByRole('option', { name: SUBJECT_FISICA.name });
    await userEvent.selectOptions(
      dialog.getByRole('combobox', {
        name: i18n.t('tutors.qualifications.subjectLabel'),
      }),
      SUBJECT_FISICA.id,
    );
    await userEvent.click(
      dialog.getByRole('button', {
        name: i18n.t('tutors.qualifications.send'),
      }),
    );
    await expect(
      await dialog.findByText(i18n.t('errors:qualification_exists')),
    ).toBeVisible();
  },
};
