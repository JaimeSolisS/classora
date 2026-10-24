import type { Meta, StoryObj } from '@storybook/react-vite';
import { expect, fn, screen, userEvent, within } from 'storybook/test';
import { ApiError } from '@/app/api';
import {
  EnrollDialog,
  type EnrollDialogProps,
} from '@/features/groups/EnrollDialog';
import { WithdrawDialog } from '@/features/groups/WithdrawDialog';
import { i18n } from '@/i18n';
import { StoryQueryClient } from '@/test/StoryQueryClient';
import { useEnrollLabels, useWithdrawLabels } from './enrollmentLabels';
import {
  COHORT_CAPACITY_CONFLICT,
  ENROLL_OVERLAP_CONFLICT,
  ENROLL_ROOM_CONFLICT,
  ENROLL_SOFT_OCCURRENCE,
  ENROLLED_ANA,
  ENROLLED_CARLA,
  ROSTER_COHORT,
  STUDENT_ANA,
  STUDENT_CARLA,
} from './fixtures';

// The cohort's enroll and withdraw dialogs (#245 E3): the picker (an
// enrolled student disabled, the started-cohort help), a soft-only 409
// with "Override and enroll", a hard 409 ("Can't be overridden", no
// action), already_enrolled, and "Withdraw" with a reason that is too
// long. No request: the submits are stubs. Axe runs in light and dark.

const meta = {
  title: 'Courses/EnrollmentDialogs',
  parameters: { layout: 'fullscreen' },
} satisfies Meta;

export default meta;
type Story = StoryObj<typeof meta>;

const ANA = { id: STUDENT_ANA.id, name: STUDENT_ANA.display_name };
const CARLA = { id: STUDENT_CARLA.id, name: STUDENT_CARLA.display_name };

function Enroll(extra: Partial<EnrollDialogProps>) {
  const labels = useEnrollLabels(ROSTER_COHORT.name);
  return (
    <StoryQueryClient>
      <EnrollDialog
        open
        groupName={ROSTER_COHORT.name}
        enrolledIds={new Set([STUDENT_ANA.id])}
        searchStudents={fn(() => Promise.resolve([ANA, CARLA]))}
        canOverride
        labels={labels}
        onEnroll={fn(() => Promise.resolve(ENROLLED_CARLA))}
        onDone={fn()}
        onCancel={fn()}
        {...extra}
      />
    </StoryQueryClient>
  );
}

export const Picker: Story = {
  render: () => (
    <Enroll help={i18n.t('courses.cohorts.enrollment.dialog.started')} />
  ),
  play: async () => {
    const dialog = within(await screen.findByRole('dialog'));
    const search = dialog.getByLabelText(i18n.t('groups.enroll.student'));
    await expect(search).toHaveFocus();
    await userEvent.type(search, 'a');
    await expect(
      await dialog.findByRole('radio', {
        name: i18n.t('courses.cohorts.enrollment.dialog.alreadyEnrolled', {
          name: ANA.name,
        }),
      }),
    ).toBeDisabled();
    await expect(
      dialog.getByText(i18n.t('courses.cohorts.enrollment.dialog.started')),
    ).toBeVisible();
  },
};

export const SoftConflictOverride: Story = {
  render: () => (
    <Enroll
      initialStudent={CARLA}
      initialConflicts={[COHORT_CAPACITY_CONFLICT, ENROLL_SOFT_OCCURRENCE]}
    />
  ),
  play: async () => {
    const dialog = within(await screen.findByRole('dialog'));
    await expect(
      dialog.getByRole('button', {
        name: i18n.t('courses.cohorts.enrollment.dialog.overrideAction'),
      }),
    ).toBeVisible();
    await expect(
      dialog.getByText(i18n.t('scheduling.conflicts.cohort_capacity_exceeded')),
    ).toBeVisible();
  },
};

export const HardConflict: Story = {
  render: () => (
    <Enroll
      initialStudent={CARLA}
      initialConflicts={[
        COHORT_CAPACITY_CONFLICT,
        ENROLL_ROOM_CONFLICT,
        ENROLL_OVERLAP_CONFLICT,
      ]}
    />
  ),
  play: async () => {
    const dialog = within(await screen.findByRole('dialog'));
    await expect(
      dialog.getAllByText(
        i18n.t('courses.cohorts.enrollment.dialog.hardLabel'),
      ),
    ).toHaveLength(2);
    await expect(
      dialog.queryByRole('button', {
        name: i18n.t('courses.cohorts.enrollment.dialog.overrideAction'),
      }),
    ).toBeNull();
  },
};

export const AlreadyEnrolled: Story = {
  render: () => (
    <Enroll
      initialStudent={CARLA}
      onEnroll={fn(() =>
        Promise.reject(
          new ApiError({
            code: 'already_enrolled',
            status: 409,
            requestId: 'req-story-enrolled',
          }),
        ),
      )}
    />
  ),
  play: async () => {
    const dialog = within(await screen.findByRole('dialog'));
    await userEvent.click(
      dialog.getByRole('button', {
        name: i18n.t('courses.cohorts.enrollment.dialog.submit'),
      }),
    );
    await expect(
      await dialog.findByText(i18n.t('errors:already_enrolled')),
    ).toBeVisible();
  },
};

function Withdraw() {
  const labels = useWithdrawLabels(ROSTER_COHORT.name);
  return (
    <WithdrawDialog
      enrollment={ENROLLED_ANA}
      groupName={ROSTER_COHORT.name}
      labels={labels}
      onConfirm={fn(() => Promise.resolve())}
      onCancel={fn()}
      onReload={fn()}
      initialReason={'x'.repeat(1001)}
    />
  );
}

export const WithdrawReasonTooLong: Story = {
  render: () => <Withdraw />,
  play: async () => {
    const dialog = within(await screen.findByRole('dialog'));
    await expect(
      dialog.getByText(
        i18n.t('courses.cohorts.enrollment.withdrawDialog.reasonTooLong'),
      ),
    ).toBeVisible();
    await expect(
      dialog.getByText(
        i18n.t('courses.cohorts.enrollment.withdrawDialog.help'),
      ),
    ).toBeVisible();
  },
};
