import type { Meta, StoryObj } from '@storybook/react-vite';
import { expect, fn, screen, userEvent, waitFor, within } from 'storybook/test';
import { ApiError } from '@/app/api';
import { LUCIA } from '@/features/my-schedule/fixtures';
import { i18n } from '@/i18n';
import { StoryQueryClient } from '@/test/StoryQueryClient';
import { StoryRouter } from '@/test/StoryRouter';
import type { ChangeRequestLoaders } from './api';
import {
  created,
  EARLY,
  FAMILY_ENTRY,
  LATE,
  POLICY,
  slotsOf,
  TUTOR_ENTRY,
  ZONE,
} from './fixtures';
import {
  RequestChangeDialog,
  type RequestChangeDialogProps,
} from './RequestChangeDialog';

// "Request a change" (#62b D3): a cancellation with an on-time and a late
// preview, a family reschedule with slots, a tutor reschedule, a `409
// slot_unavailable` and the 320 px layout. The loaders are stubs: no
// request leaves the story. Axe runs in light and dark (the Storybook
// projects).

function loaders(
  create: ChangeRequestLoaders['createChangeRequest'] = () =>
    Promise.resolve(created()),
): ChangeRequestLoaders {
  return {
    fetchPendingRequest: () => Promise.resolve(null),
    fetchCurrentPolicy: () => Promise.resolve(POLICY),
    fetchSlots: (query) => Promise.resolve(slotsOf(query.from, query.to)),
    createChangeRequest: create,
  };
}

function DialogStory(props: RequestChangeDialogProps) {
  return (
    <StoryQueryClient>
      <StoryRouter
        routes={[
          {
            path: '/',
            element: (
              <main className="p-4">
                <h1 className="text-2xl font-semibold">
                  {i18n.t('mySchedule.title')}
                </h1>
                <RequestChangeDialog {...props} />
              </main>
            ),
          },
        ]}
      />
    </StoryQueryClient>
  );
}

const meta = {
  title: 'Change requests/RequestChangeDialog',
  component: DialogStory,
  args: {
    open: true,
    entry: FAMILY_ENTRY,
    studentId: LUCIA.student_id,
    timeZone: ZONE,
    now: EARLY,
    clock: () => EARLY,
    loaders: loaders(),
    onClose: fn(),
    onSent: fn(),
    initialStep: 2,
    initialKind: 'cancellation',
  },
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
} satisfies Meta<typeof DialogStory>;

export default meta;
type Story = StoryObj<typeof meta>;

async function preview(key: string) {
  const dialog = await screen.findByRole('dialog');
  await waitFor(() =>
    expect(
      within(dialog).getByText(i18n.t('changeRequests.preview.heading')),
    ).toBeVisible(),
  );
  // The text up to the (empty) makeup date.
  const expected = (
    i18n.t as unknown as (key: string, options: object) => string
  )(key, { hours: 24, date: '' }).trim();
  await expect(dialog.textContent).toContain(expected);
  return dialog;
}

export const ChooseWhat: Story = {
  args: { initialStep: 1 },
  play: async () => {
    const dialog = await screen.findByRole('dialog');
    await expect(
      within(dialog).getByRole('radio', {
        name: i18n.t('changeRequests.dialog.cancel'),
      }),
    ).toBeChecked();
    await expect(
      within(dialog).getByRole('radio', {
        name: i18n.t('changeRequests.dialog.move'),
      }),
    ).toBeEnabled();
  },
};

export const CancelOnTime: Story = {
  play: async () => {
    await preview('changeRequests.preview.onTimeMakeup');
  },
};

export const CancelLate: Story = {
  args: { now: LATE, clock: () => LATE },
  play: async () => {
    await preview('changeRequests.preview.lateNoMakeup');
  },
};

export const FamilyReschedule: Story = {
  args: { initialKind: 'reschedule' },
  play: async () => {
    const dialog = await screen.findByRole('dialog');
    const slots = await within(dialog).findAllByRole('button', {
      pressed: false,
    });
    await userEvent.click(slots[0]!);
    await expect(
      within(dialog).getAllByRole('button', { pressed: true }),
    ).toHaveLength(1);
  },
};

export const TutorReschedule: Story = {
  args: {
    entry: TUTOR_ENTRY,
    studentId: null,
    initialKind: 'reschedule',
  },
  play: async () => {
    const dialog = await screen.findByRole('dialog');
    await expect(
      within(dialog).getByLabelText(i18n.t('changeRequests.dialog.date')),
    ).toBeVisible();
    await expect(
      within(dialog).getByLabelText(i18n.t('changeRequests.dialog.time')),
    ).toBeVisible();
    await preview('changeRequests.preview.excused');
  },
};

export const SlotUnavailable: Story = {
  args: {
    initialKind: 'reschedule',
    loaders: loaders(() =>
      Promise.reject(
        new ApiError({
          code: 'slot_unavailable',
          status: 409,
          requestId: 'req-1',
        }),
      ),
    ),
  },
  play: async () => {
    const dialog = await screen.findByRole('dialog');
    const slots = await within(dialog).findAllByRole('button', {
      pressed: false,
    });
    await userEvent.click(slots[0]!);
    await userEvent.click(
      within(dialog).getByRole('button', {
        name: i18n.t('changeRequests.dialog.send'),
      }),
    );
    await expect(await within(dialog).findByRole('alert')).toHaveTextContent(
      i18n.t('changeRequests.errors.slot_unavailable'),
    );
  },
};

export const Narrow: Story = {
  globals: { viewport: { value: 'narrow320' } },
  args: { initialKind: 'reschedule' },
  play: async () => {
    const dialog = await screen.findByRole('dialog');
    await within(dialog).findAllByRole('button', { pressed: false });
    await expect(dialog.scrollWidth).toBeLessThanOrEqual(dialog.clientWidth);
  },
};
