import type { Meta, StoryObj } from '@storybook/react-vite';
import { expect, fireEvent, fn, screen, within } from 'storybook/test';
import { ApiError } from '@/app/api';
import { i18n } from '@/i18n';
import { StoryQueryClient } from '@/test/StoryQueryClient';
import { StoryRouter } from '@/test/StoryRouter';
import type { AvailabilityChangeRequest } from './api';
import { AvailabilitySection } from './AvailabilitySection';
import {
  AVAILABILITY,
  AVAILABILITY_WITH_CHANGES,
  CLASSROOM_OPTIONS,
  NO_AVAILABILITY,
  TIME_OFF_CHANGE,
  WEEKLY_CHANGE,
} from './fixtures';

// The tutor profile's Availability section (#45 F4): editable (the tutor or
// tutor.manage) and read-only (a Coordinator), the empty states, and the
// exception dialog with its field errors; the teaching limits and preferred
// classrooms (#197 C2): editable with an inactive stored room, read-only,
// field errors, and a server rejection. M4-18 (#63 H4): time off sent for
// approval (202), the "Sent for approval" notice, the pending changes with
// Withdraw (and a 409 in its dialog), read-only, a rejected change's
// reason, and the section at 320 px. No request: every call is a stub.

/** A write that applied (no change request). */
const NOT_SENT = (): Promise<AvailabilityChangeRequest | null> =>
  Promise.resolve(null);

const meta = {
  title: 'Tutors/AvailabilitySection',
  component: AvailabilitySection,
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
  args: {
    state: { status: 'ready', availability: AVAILABILITY },
    canEdit: true,
    timeZone: 'America/Mexico_City',
    onSaveWeekly: fn(NOT_SENT),
    onReload: fn(() => Promise.resolve()),
    classrooms: { status: 'ready', items: CLASSROOM_OPTIONS },
    onSaveSettings: fn(() => Promise.resolve()),
    onAddException: fn(NOT_SENT),
    onDeleteException: fn(NOT_SENT),
    onWithdrawChange: fn(() => Promise.resolve()),
  },
  render: (args) => (
    <StoryQueryClient>
      <StoryRouter
        routes={[
          {
            path: '*',
            element: (
              <main>
                <AvailabilitySection {...args} />
              </main>
            ),
          },
        ]}
      />
    </StoryQueryClient>
  ),
} satisfies Meta<typeof AvailabilitySection>;

export default meta;
type Story = StoryObj<typeof meta>;

export const Editable: Story = {
  play: async ({ canvas }) => {
    await expect(
      canvas.getByRole('heading', {
        level: 2,
        name: i18n.t('tutors.availability.title'),
      }),
    ).toBeVisible();
    await expect(
      canvas.getByRole('button', { name: i18n.t('tutors.availability.save') }),
    ).toBeVisible();
    await expect(
      canvas.getByText(i18n.t('tutors.availability.kinds.unavailable')),
    ).toBeVisible();
  },
};

export const ReadOnly: Story = {
  args: { canEdit: false },
  play: async ({ canvas }) => {
    await expect(canvas.queryAllByRole('button')).toHaveLength(0);
    await expect(canvas.getByText('16:00–20:00')).toBeVisible();
  },
};

export const Empty: Story = {
  args: {
    canEdit: false,
    state: { status: 'ready', availability: NO_AVAILABILITY },
  },
  play: async ({ canvas }) => {
    await expect(
      canvas.getByText(i18n.t('tutors.availability.weeklyEmpty')),
    ).toBeVisible();
    await expect(
      canvas.getByText(i18n.t('tutors.availability.exceptionsEmpty')),
    ).toBeVisible();
  },
};

export const EmptyEditable: Story = {
  args: { state: { status: 'ready', availability: NO_AVAILABILITY } },
};

export const ExceptionDialogErrors: Story = {
  args: { initiallyAdding: true },
  play: async ({ userEvent }) => {
    const dialog = within(await screen.findByRole('dialog'));
    await userEvent.selectOptions(
      dialog.getByRole('combobox', {
        name: i18n.t('tutors.availability.kindLabel'),
      }),
      'available',
    );
    await userEvent.click(
      dialog.getByRole('button', { name: i18n.t('tutors.availability.send') }),
    );
    await expect(
      dialog.getAllByText(
        i18n.t('tutors.availability.validation.dateRequired'),
      ),
    ).toHaveLength(2);
    await expect(
      dialog.getAllByText(
        i18n.t('tutors.availability.validation.timeRequired'),
      ),
    ).toHaveLength(2);
  },
};

export const ExceptionDialogSentForApproval: Story = {
  args: {
    initiallyAdding: true,
    onAddException: fn(() => Promise.resolve(TIME_OFF_CHANGE)),
  },
  play: async ({ canvas, userEvent }) => {
    const dialog = within(await screen.findByRole('dialog'));
    // Date inputs take the whole value at once.
    for (const label of ['startsOn', 'endsOn'] as const) {
      await fireEvent.change(
        dialog.getByLabelText(i18n.t(`tutors.availability.${label}`)),
        { target: { value: '2027-01-11' } },
      );
    }
    await userEvent.click(
      dialog.getByRole('button', { name: i18n.t('tutors.availability.send') }),
    );
    await expect(
      await canvas.findByText(
        i18n.t('tutors.availability.changes.sent', { count: 1 }),
      ),
    ).toBeVisible();
    await expect(screen.queryByRole('dialog')).toBeNull();
  },
};

export const SentForApproval: Story = {
  args: { initiallySent: WEEKLY_CHANGE },
  play: async ({ canvas }) => {
    await expect(
      canvas.getByText(
        i18n.t('tutors.availability.changes.sent', { count: 5 }),
      ),
    ).toBeVisible();
    await expect(
      canvas.getByText(
        i18n.t('tutors.availability.changes.more', { count: 2 }),
      ),
    ).toBeVisible();
  },
};

export const PendingChanges: Story = {
  args: {
    state: { status: 'ready', availability: AVAILABILITY_WITH_CHANGES },
  },
  play: async ({ canvas }) => {
    await expect(
      canvas.getByRole('heading', {
        name: i18n.t('tutors.availability.changes.pendingTitle'),
      }),
    ).toBeVisible();
    // Each button's accessible name names its change.
    await expect(
      canvas.getAllByText(i18n.t('tutors.availability.changes.withdraw')),
    ).toHaveLength(2);
    await expect(
      canvas.getByText(
        i18n.t('tutors.availability.changes.reason', {
          reason: 'No hay sustituto para esas clases.',
        }),
      ),
    ).toBeVisible();
  },
};

export const PendingChangesReadOnly: Story = {
  args: {
    canEdit: false,
    state: { status: 'ready', availability: AVAILABILITY_WITH_CHANGES },
  },
  play: async ({ canvas }) => {
    await expect(canvas.queryAllByRole('button')).toHaveLength(0);
    await expect(
      canvas.getByText(
        i18n.t('tutors.availability.changes.affected', { count: 5 }),
      ),
    ).toBeVisible();
  },
};

export const WithdrawNotPending: Story = {
  args: {
    state: { status: 'ready', availability: AVAILABILITY_WITH_CHANGES },
    onWithdrawChange: fn(() =>
      Promise.reject(
        new ApiError({ code: 'availability_change_not_pending', status: 409 }),
      ),
    ),
  },
  play: async ({ canvas, userEvent }) => {
    const [first] = canvas.getAllByText(
      i18n.t('tutors.availability.changes.withdraw'),
    );
    await userEvent.click(first!);
    const dialog = within(await screen.findByRole('dialog'));
    await userEvent.click(
      dialog.getByRole('button', {
        name: i18n.t('tutors.availability.changes.withdrawConfirm'),
      }),
    );
    await expect(
      await dialog.findByText(i18n.t('errors:availability_change_not_pending')),
    ).toBeVisible();
  },
};

export const PendingChangesNarrow: Story = {
  args: {
    state: { status: 'ready', availability: AVAILABILITY_WITH_CHANGES },
    initiallySent: WEEKLY_CHANGE,
  },
  globals: { viewport: { value: 'narrow320' } },
};

export const LimitsAndRooms: Story = {
  play: async ({ canvas }) => {
    await expect(
      canvas.getByText(i18n.t('tutors.availability.settings.limitsHelp')),
    ).toBeVisible();
    await expect(
      canvas.getByRole('checkbox', {
        name: i18n.t('tutors.availability.settings.inactiveRoom', {
          name: 'Aula Vieja',
        }),
      }),
    ).toBeChecked();
    await expect(
      canvas.getByRole('checkbox', { name: 'Aula 2' }),
    ).not.toBeChecked();
  },
};

export const LimitsReadOnly: Story = {
  args: { canEdit: false },
  play: async ({ canvas }) => {
    await expect(canvas.getByText('4 h')).toBeVisible();
    await expect(canvas.getByText('20 h')).toBeVisible();
    await expect(canvas.getByText('15 min')).toBeVisible();
    await expect(
      canvas.getByText(
        i18n.t('tutors.availability.settings.inactiveRoom', {
          name: 'Aula Vieja',
        }),
      ),
    ).toBeVisible();
  },
};

export const LimitsLoadingRooms: Story = {
  args: {
    classrooms: { status: 'loading' },
    state: { status: 'ready', availability: NO_AVAILABILITY },
  },
};

export const LimitsFieldErrors: Story = {
  play: async ({ canvas, userEvent }) => {
    const daily = canvas.getByRole('group', {
      name: i18n.t('tutors.availability.settings.daily'),
    });
    const weekly = canvas.getByRole('group', {
      name: i18n.t('tutors.availability.settings.weekly'),
    });
    const hours = i18n.t('tutors.availability.settings.hoursLabel');
    await userEvent.clear(within(daily).getByLabelText(hours));
    await userEvent.type(within(daily).getByLabelText(hours), '25');
    await userEvent.clear(within(weekly).getByLabelText(hours));
    await userEvent.type(within(weekly).getByLabelText(hours), '2');
    await userEvent.click(
      canvas.getByRole('button', {
        name: i18n.t('tutors.availability.settings.save'),
      }),
    );
    await expect(
      canvas.getByText(
        i18n.t('tutors.availability.settings.validation.dailyRange'),
      ),
    ).toBeVisible();
  },
};

export const LimitsServerRejection: Story = {
  args: {
    onSaveSettings: fn(() =>
      Promise.reject(
        new ApiError({
          code: 'validation_error',
          status: 422,
          fieldErrors: [
            {
              path: ['body', 'preferred_classroom_ids'],
              code: 'classroom_unavailable',
              message: 'Choose only existing, active classrooms.',
            },
          ],
        }),
      ),
    ),
  },
  play: async ({ canvas, userEvent }) => {
    await userEvent.click(canvas.getByRole('checkbox', { name: 'Aula 2' }));
    await userEvent.click(
      canvas.getByRole('button', {
        name: i18n.t('tutors.availability.settings.save'),
      }),
    );
    await expect(
      await canvas.findByText(
        i18n.t('tutors.availability.settings.validation.roomUnavailable'),
      ),
    ).toBeVisible();
  },
};
