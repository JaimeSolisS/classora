import type { Meta, StoryObj } from '@storybook/react-vite';
import { expect, fn, screen, within } from 'storybook/test';
import { ApiError } from '@/app/api';
import { i18n } from '@/i18n';
import { StoryRouter } from '@/test/StoryRouter';
import type { AvailableSlot } from './api';
import {
  CANCUN,
  PHYSICS,
  STUDENT_ANA,
  STUDENT_LUIS,
  SUBJECTS,
  TODAY,
  WEEK_SLOTS,
} from './fixtures';
import {
  RequestSessionView,
  type RequestSessionViewProps,
} from './RequestSessionView';

// "Request a session" (#59 F5): loading, the week with slots (one already
// requested), a week without slots, two children, the confirmation dialog,
// a slot_unavailable answer in it, the success panel, and the 320 px
// layout. No request: the answers are stubs. Axe runs in light and dark.

const FIRST_SLOT = WEEK_SLOTS.days[0]!.slots[0] as AvailableSlot;

function RequestStory(props: RequestSessionViewProps) {
  return (
    <StoryRouter
      routes={[
        {
          path: '/',
          element: (
            <main className="p-4">
              <h1 className="mb-4 text-2xl font-semibold">
                {i18n.t('sessionRequests.new.title')}
              </h1>
              <RequestSessionView {...props} />
            </main>
          ),
        },
      ]}
    />
  );
}

const meta = {
  title: 'Family portal/RequestSessionView',
  component: RequestStory,
  args: {
    students: { status: 'ready', data: [STUDENT_ANA] },
    subjects: { status: 'ready', data: SUBJECTS },
    studentId: STUDENT_ANA.id,
    onStudent: fn(),
    subjectId: PHYSICS.id,
    onSubject: fn(),
    duration: 60,
    onDuration: fn(),
    weekStart: TODAY,
    today: TODAY,
    onWeek: fn(),
    slots: { status: 'ready', data: WEEK_SLOTS },
    timeZone: CANCUN,
    onRequest: fn(() => Promise.resolve()),
    sent: false,
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
} satisfies Meta<typeof RequestStory>;

export default meta;
type Story = StoryObj<typeof meta>;

export const Loading: Story = {
  args: { students: { status: 'loading' } },
};

export const SlotsLoading: Story = {
  args: { slots: { status: 'loading' } },
};

export const Slots: Story = {
  play: async ({ canvasElement }) => {
    const canvas = within(canvasElement);
    const requested = canvas.getByRole('button', {
      name: /Requested|Solicitado/,
    });
    await expect(requested).toBeDisabled();
    await expect(
      canvas.getAllByText(i18n.t('sessionRequests.new.noSlots')),
    ).toHaveLength(6);
  },
};

export const NoSlotsThisWeek: Story = {
  args: {
    slots: {
      status: 'ready',
      data: {
        ...WEEK_SLOTS,
        days: WEEK_SLOTS.days.map((day) => ({ ...day, slots: [] })),
      },
    },
  },
};

export const TwoChildren: Story = {
  args: {
    students: { status: 'ready', data: [STUDENT_ANA, STUDENT_LUIS] },
    studentId: null,
    slots: { status: 'idle' },
  },
  play: async ({ canvasElement }) => {
    await expect(within(canvasElement).getAllByRole('radio')).toHaveLength(7);
  },
};

export const ConfirmDialog: Story = {
  args: { initialSlot: FIRST_SLOT },
  play: async () => {
    const dialog = await screen.findByRole('dialog');
    await expect(
      within(dialog).getByText(i18n.t('sessionRequests.confirm.notBooking')),
    ).toBeVisible();
  },
};

export const SlotUnavailable: Story = {
  args: {
    initialSlot: FIRST_SLOT,
    initialFailure: new ApiError({
      code: 'slot_unavailable',
      status: 409,
      requestId: 'req-slot',
    }),
  },
  play: async () => {
    const dialog = await screen.findByRole('dialog');
    await expect(within(dialog).getByRole('alert')).toBeVisible();
  },
};

export const ConsentRequired: Story = {
  args: {
    slots: {
      status: 'error',
      error: new ApiError({
        code: 'student_consent_required',
        status: 403,
        requestId: 'req-consent',
      }),
      onRetry: fn(),
    },
  },
};

export const Sent: Story = {
  args: { sent: true },
  play: async ({ canvasElement }) => {
    await expect(
      within(canvasElement).getByText(i18n.t('sessionRequests.new.sent')),
    ).toBeVisible();
  },
};

export const Narrow: Story = {
  globals: { viewport: { value: 'narrow320' } },
  play: async ({ canvasElement }) => {
    const main = canvasElement.querySelector('main')!;
    await expect(main.scrollWidth).toBeLessThanOrEqual(main.clientWidth);
  },
};
