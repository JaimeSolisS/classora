import type { Meta, StoryObj } from '@storybook/react-vite';
import { expect, fireEvent, fn, screen, within } from 'storybook/test';
import { ApiError } from '@/app/api';
import { i18n } from '@/i18n';
import { StoryQueryClient } from '@/test/StoryQueryClient';
import { StoryRouter } from '@/test/StoryRouter';
import { AvailabilitySection } from './AvailabilitySection';
import { AVAILABILITY, NO_AVAILABILITY } from './fixtures';

// The tutor profile's Availability section (#45 F4): editable (the tutor or
// tutor.manage) and read-only (a Coordinator), the empty states, and the
// exception dialog with its field errors and a requires-approval answer.
// No request: every call is a stub.

const meta = {
  title: 'Tutors/AvailabilitySection',
  component: AvailabilitySection,
  args: {
    state: { status: 'ready', availability: AVAILABILITY },
    canEdit: true,
    timeZone: 'America/Mexico_City',
    onSaveWeekly: fn(() => Promise.resolve()),
    onReload: fn(() => Promise.resolve()),
    onAddException: fn(() => Promise.resolve()),
    onDeleteException: fn(() => Promise.resolve()),
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

export const ExceptionDialogNeedsApproval: Story = {
  args: {
    initiallyAdding: true,
    onAddException: fn(() =>
      Promise.reject(
        new ApiError({
          code: 'availability_change_requires_approval',
          status: 409,
        }),
      ),
    ),
  },
  play: async ({ userEvent }) => {
    const dialog = within(await screen.findByRole('dialog'));
    // Date inputs take the whole value at once.
    for (const label of ['startsOn', 'endsOn'] as const) {
      fireEvent.change(
        dialog.getByLabelText(i18n.t(`tutors.availability.${label}`)),
        { target: { value: '2026-12-21' } },
      );
    }
    await userEvent.click(
      dialog.getByRole('button', { name: i18n.t('tutors.availability.send') }),
    );
    await expect(
      await dialog.findByText(
        i18n.t('errors:availability_change_requires_approval'),
      ),
    ).toBeVisible();
  },
};
