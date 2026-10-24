import type { Meta, StoryObj } from '@storybook/react-vite';
import { expect, fn, within } from 'storybook/test';
import { i18n } from '@/i18n';
import { StoryRouter } from '@/test/StoryRouter';
import { EMPTY_ROSTER_COHORT, FULL_COHORT, ROSTER_COHORT } from './fixtures';
import { RosterPanel, type RosterPanelProps } from './RosterPanel';

// The cohort's Students panel (#245 E3): empty, partly full, full with a
// room limit, with the withdrawn rows shown (also at 320 px), and
// read-only for a Coordinator or Finance. No request; axe runs in light
// and dark.

const ZONE = 'America/Mexico_City';

function PanelStory(props: RosterPanelProps) {
  return (
    <StoryRouter
      routes={[
        {
          path: '/',
          element: (
            <main className="p-4">
              <h1 className="mb-4 text-2xl font-semibold">
                {props.cohort.name}
              </h1>
              <RosterPanel {...props} />
            </main>
          ),
        },
      ]}
    />
  );
}

const meta = {
  title: 'Courses/RosterPanel',
  component: PanelStory,
  args: {
    cohort: ROSTER_COHORT,
    roster: ROSTER_COHORT.roster!,
    timeZone: ZONE,
    canManage: true,
    linkStudents: true,
    showWithdrawn: false,
    onShowWithdrawnChange: fn(),
    onEnroll: fn(),
    onWithdraw: fn(),
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
} satisfies Meta<typeof PanelStory>;

export default meta;
type Story = StoryObj<typeof meta>;

export const Empty: Story = {
  args: { cohort: EMPTY_ROSTER_COHORT, roster: [] },
  play: async ({ canvasElement }) => {
    const canvas = within(canvasElement);
    await expect(
      canvas.getByText(i18n.t('courses.cohorts.enrollment.none')),
    ).toBeVisible();
    await expect(
      canvas.getByRole('button', {
        name: i18n.t('courses.cohorts.enrollment.enroll'),
      }),
    ).toBeVisible();
  },
};

export const PartlyFull: Story = {
  play: async ({ canvasElement }) => {
    const canvas = within(canvasElement);
    await expect(
      canvas.getByText(
        i18n.t('courses.cohorts.enrollment.capacity', {
          enrolled: 2,
          max: 12,
        }),
      ),
    ).toBeVisible();
    await expect(
      canvas.queryByText(i18n.t('courses.cohorts.enrollment.full')),
    ).toBeNull();
  },
};

export const FullWithRoomLimit: Story = {
  args: { cohort: FULL_COHORT, roster: FULL_COHORT.roster! },
  play: async ({ canvasElement }) => {
    const canvas = within(canvasElement);
    await expect(
      canvas.getByText(
        i18n.t('courses.cohorts.enrollment.roomLimit', { capacity: 8 }),
      ),
    ).toBeVisible();
    await expect(
      canvas.getByText(i18n.t('courses.cohorts.enrollment.full')),
    ).toBeVisible();
  },
};

export const WithdrawnShown: Story = {
  args: { showWithdrawn: true },
  play: async ({ canvasElement }) => {
    const canvas = within(canvasElement);
    await expect(
      canvas.getByRole('checkbox', {
        name: i18n.t('courses.cohorts.enrollment.showWithdrawn'),
      }),
    ).toBeChecked();
    await expect(
      canvas.getByText(i18n.t('courses.cohorts.enrollment.noReason')),
    ).toBeVisible();
  },
};

// WCAG reflow: at 320 px the tables scroll inside their own regions; the
// page doesn't scroll sideways.
export const WithdrawnShownNarrow: Story = {
  args: { showWithdrawn: true },
  globals: { viewport: { value: 'narrow320' } },
  play: async ({ canvasElement }) => {
    await expect(
      canvasElement.ownerDocument.documentElement.scrollWidth,
    ).toBeLessThanOrEqual(320);
  },
};

export const ReadOnly: Story = {
  args: { canManage: false, linkStudents: false },
  play: async ({ canvasElement }) => {
    const canvas = within(canvasElement);
    await expect(
      canvas.queryByRole('button', {
        name: i18n.t('courses.cohorts.enrollment.enroll'),
      }),
    ).toBeNull();
    await expect(
      canvas.queryByRole('button', {
        name: i18n.t('courses.cohorts.enrollment.withdrawOf', {
          name: 'Ana López',
        }),
      }),
    ).toBeNull();
  },
};
