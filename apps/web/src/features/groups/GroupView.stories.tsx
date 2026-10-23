import type { Meta, StoryObj } from '@storybook/react-vite';
import { expect, fn, within } from 'storybook/test';
import { i18n } from '@/i18n';
import { StoryRouter } from '@/test/StoryRouter';
import {
  ENDED_GROUP,
  GROUP,
  LEAKY_STUDENT_GROUP,
  ROOM_LIMITED_GROUP,
  SESSIONS,
} from './fixtures';
import { GroupView, type GroupViewProps } from './GroupView';
import { MySessionsSection } from './MySessionsSection';

// The group page (#234 G2): staff with the actions, staff on an ended
// group (no "Enroll student"), a room that holds fewer than the maximum,
// read-only (a Coordinator: no buttons), and the student view ("My
// sessions"; never the roster, the count or the next sessions, even when
// the response carries them). No request; axe runs in light and dark.

const ZONE = 'America/Mexico_City';

function GroupStory(props: GroupViewProps) {
  return (
    <StoryRouter
      routes={[
        {
          path: '/',
          element: (
            <main className="p-4">
              <h1 className="mb-4 text-2xl font-semibold">
                {props.group.name}
              </h1>
              <GroupView {...props} />
            </main>
          ),
        },
      ]}
    />
  );
}

const meta = {
  title: 'Groups/GroupView',
  component: GroupStory,
  args: {
    group: GROUP,
    view: 'staff',
    timeZone: ZONE,
    canManage: true,
    onEnroll: fn(),
    onWithdraw: fn(),
    onEdit: fn(),
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
} satisfies Meta<typeof GroupStory>;

export default meta;
type Story = StoryObj<typeof meta>;

export const StaffActive: Story = {
  play: async ({ canvasElement }) => {
    const canvas = within(canvasElement);
    await expect(
      canvas.getByRole('button', { name: i18n.t('groups.page.enroll') }),
    ).toBeVisible();
    await expect(
      canvas.getByText(i18n.t('groups.roster.overridden')),
    ).toBeVisible();
  },
};

export const StaffEnded: Story = {
  args: { group: ENDED_GROUP },
  play: async ({ canvasElement }) => {
    const canvas = within(canvasElement);
    await expect(canvas.getByText(i18n.t('groups.ended'))).toBeVisible();
    await expect(
      canvas.queryByRole('button', { name: i18n.t('groups.page.enroll') }),
    ).toBeNull();
  },
};

export const RoomLimited: Story = {
  args: { group: ROOM_LIMITED_GROUP },
  play: async ({ canvasElement }) => {
    await expect(
      within(canvasElement).getByText(
        i18n.t('groups.capacity.roomLimit', { capacity: 4 }),
      ),
    ).toBeVisible();
  },
};

export const ReadOnly: Story = {
  args: { canManage: false },
  play: async ({ canvasElement }) => {
    await expect(within(canvasElement).queryAllByRole('button')).toHaveLength(
      0,
    );
  },
};

// #234 QA D2: at 320 px the roster tables scroll sideways and, read-only,
// hold nothing focusable; each scroll container takes the focus as a region
// named by its heading, so axe (scrollable-region-focusable) passes.
export const ReadOnlyNarrow: Story = {
  args: { canManage: false },
  globals: { viewport: { value: 'narrow320' } },
  play: async ({ canvasElement }) => {
    const canvas = within(canvasElement);
    const withdrawn = canvas.getByRole('region', {
      name: i18n.t('groups.roster.withdrawn', { count: 2 }),
    });
    await expect(withdrawn).toHaveAttribute('tabindex', '0');
    await expect(withdrawn.scrollWidth).toBeGreaterThan(withdrawn.clientWidth);
  },
};

export const Student: Story = {
  args: {
    group: LEAKY_STUDENT_GROUP,
    view: 'student',
    canManage: false,
    mySessions: (
      <MySessionsSection
        windowStart="2030-12-21"
        state={{ status: 'ready', items: SESSIONS }}
        timeZone={ZONE}
        onPrevious={fn()}
        onNext={fn()}
      />
    ),
  },
  play: async ({ canvasElement }) => {
    const canvas = within(canvasElement);
    await expect(
      canvas.getByRole('heading', {
        name: i18n.t('groups.mySessions.heading'),
      }),
    ).toBeVisible();
    await expect(canvas.queryByText('Bruno Díaz')).toBeNull();
    await expect(
      canvas.queryByRole('heading', { name: i18n.t('groups.next.heading') }),
    ).toBeNull();
  },
};
