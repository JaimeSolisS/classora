import type { Meta, StoryObj } from '@storybook/react-vite';
import { expect, fn, screen, within } from 'storybook/test';
import { i18n } from '@/i18n';
import { StoryQueryClient } from '@/test/StoryQueryClient';
import { EditGroupDialog } from './EditGroupDialog';
import { EnrollDialog } from './EnrollDialog';
import {
  ACTIVE_ANA,
  CAPACITY_CONFLICT,
  ENROLL_OVERLAP_CONFLICT,
  ENROLL_ROOM_CONFLICT,
  ENROLLED,
  GROUP,
  STUDENT_CARLA,
} from './fixtures';
import { WithdrawDialog } from './WithdrawDialog';

// The group dialogs (#234 G2): "Enroll student" empty, with a soft-only
// 409 (the override button), with a hard one (no button), and the override
// dialog over it; "Withdraw" with a reason that is too long; "Edit" with
// the below-enrolled error. No request: the submits are stubs. Axe runs in
// light and dark.

const meta = {
  title: 'Groups/GroupDialogs',
  parameters: { layout: 'fullscreen' },
} satisfies Meta;

export default meta;
type Story = StoryObj<typeof meta>;

const CARLA = { id: STUDENT_CARLA.id, name: STUDENT_CARLA.display_name };

function enroll(extra: Partial<Parameters<typeof EnrollDialog>[0]>) {
  return (
    <StoryQueryClient>
      <EnrollDialog
        open
        groupName={GROUP.name}
        enrolledIds={new Set([ACTIVE_ANA.student.id])}
        searchStudents={fn(() => Promise.resolve([CARLA]))}
        canOverride
        onEnroll={fn(() => Promise.resolve(ENROLLED))}
        onDone={fn()}
        onCancel={fn()}
        {...extra}
      />
    </StoryQueryClient>
  );
}

export const EnrollEmpty: Story = {
  render: () => enroll({}),
  play: async () => {
    const dialog = within(await screen.findByRole('dialog'));
    await expect(
      dialog.getByLabelText(i18n.t('groups.enroll.student')),
    ).toHaveFocus();
  },
};

export const EnrollSoftConflict: Story = {
  render: () =>
    enroll({ initialStudent: CARLA, initialConflicts: [CAPACITY_CONFLICT] }),
  play: async () => {
    const dialog = within(await screen.findByRole('dialog'));
    await expect(
      dialog.getByRole('button', {
        name: i18n.t('groups.enroll.overrideAction'),
      }),
    ).toBeVisible();
  },
};

export const EnrollHardConflict: Story = {
  render: () =>
    enroll({
      initialStudent: CARLA,
      initialConflicts: [
        CAPACITY_CONFLICT,
        ENROLL_ROOM_CONFLICT,
        ENROLL_OVERLAP_CONFLICT,
      ],
    }),
  play: async () => {
    const dialog = within(await screen.findByRole('dialog'));
    await expect(
      dialog.getByText(i18n.t('scheduling.override.fixHardFirst')),
    ).toBeVisible();
  },
};

export const EnrollOverrideDialog: Story = {
  render: () =>
    enroll({
      initialStudent: CARLA,
      initialConflicts: [CAPACITY_CONFLICT],
      initialReason: 'Hermana de una alumna inscrita.',
    }),
  play: async () => {
    const override = within(
      await screen.findByRole('dialog', {
        name: i18n.t('groups.enroll.overrideAction'),
      }),
    );
    await expect(
      override.getByText(i18n.t('groups.enroll.overrideDescription')),
    ).toBeVisible();
  },
};

export const WithdrawReasonTooLong: Story = {
  render: () => (
    <WithdrawDialog
      enrollment={ACTIVE_ANA}
      groupName={GROUP.name}
      onConfirm={fn(() => Promise.resolve())}
      onCancel={fn()}
      onReload={fn()}
      initialReason={'x'.repeat(1001)}
    />
  ),
  play: async () => {
    const dialog = within(await screen.findByRole('dialog'));
    await expect(
      dialog.getByText(i18n.t('groups.withdraw.reasonTooLong')),
    ).toBeVisible();
  },
};

export const EditBelowEnrolled: Story = {
  render: () => (
    <EditGroupDialog
      open
      group={GROUP}
      roomCapacity={10}
      onSubmit={fn(() => Promise.resolve())}
      onCancel={fn()}
      onReload={fn()}
      initialValues={{ name: GROUP.name, max: '1' }}
      initialErrors={{ max: 'groups.edit.belowEnrolled' }}
    />
  ),
  play: async () => {
    const dialog = within(await screen.findByRole('dialog'));
    await expect(
      dialog.getByText(i18n.t('groups.edit.belowEnrolled', { count: 2 })),
    ).toBeVisible();
  },
};
