import type { Meta, StoryObj } from '@storybook/react-vite';
import { expect, fn, screen, within } from 'storybook/test';
import { TUTORS } from '@/features/scheduling/create/fixtures';
import { OfferPanel } from '@/features/scheduling/session/OfferPanel';
import { i18n } from '@/i18n';
import { StoryRouter } from '@/test/StoryRouter';
import { CohortSessionsSection } from './CohortSessionsSection';
import { CohortView, type CohortViewProps } from './CohortView';
import { EditCohortDialog } from './EditCohortDialog';
import { TutorRosterSection } from '@/features/groups/TutorRosterSection';
import {
  COHORT,
  ENDED_COHORT,
  FAMILY_COHORT,
  FAMILY_SESSIONS,
  PENDING_COHORT,
  SESSIONS,
  TUTOR_COHORT,
  TUTORED_COHORT,
} from './fixtures';
import { ApiError } from '@/app/api';

// The cohort page (#243 E3): with a tutor (a room that limits the
// capacity), with a pending offer, without a tutor and the offer panel
// open (also at 320 px), an ended cohort, and the edit dialog after a
// version conflict. #246 B6: a student (the syllabus, sessions linked to
// "My schedule"), a guardian of two (the "Student" column), the cohort's
// tutor ("Students" by name), at 320 px, and no sessions. No request; axe
// runs in light and dark.

const ZONE = 'America/Mexico_City';

function CohortStory(props: CohortViewProps) {
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
              <CohortView {...props} />
            </main>
          ),
        },
      ]}
    />
  );
}

const sessions = (
  <CohortSessionsSection
    windowStart="2031-01-04"
    state={{ status: 'ready', items: SESSIONS }}
    timeZone={ZONE}
    linkSessions
    onPrevious={fn()}
    onNext={fn()}
  />
);

const meta = {
  title: 'Courses/CohortView',
  component: CohortStory,
  args: {
    cohort: TUTORED_COHORT,
    timeZone: ZONE,
    canManage: true,
    linkTemplate: true,
    linkSessions: true,
    onEdit: fn(),
    children: sessions,
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
} satisfies Meta<typeof CohortStory>;

export default meta;
type Story = StoryObj<typeof meta>;

export const WithTutor: Story = {
  play: async ({ canvasElement }) => {
    await expect(
      within(canvasElement).getByText(
        i18n.t('courses.cohorts.page.roomLimited', { capacity: 8 }),
      ),
    ).toBeVisible();
  },
};

export const PendingOffer: Story = {
  args: { cohort: PENDING_COHORT },
};

const offerPanel = (
  <OfferPanel
    tutors={TUTORS}
    timeZone={ZONE}
    canOverride={false}
    onSuggest={fn(() => Promise.resolve({ items: [], eligible_count: 0 }))}
    onSubmit={fn(() => Promise.reject(new Error('story')))}
    onOffered={fn()}
    series={{
      onSubmit: fn(() => Promise.reject(new Error('story'))),
      only: {
        help: i18n.t('courses.cohorts.tutor.offerHelp', {
          date: 'January 6, 2031',
        }),
        noOverride: i18n.t('courses.cohorts.tutor.noOverride'),
        submit: i18n.t('courses.cohorts.tutor.submit'),
      },
    }}
  />
);

export const WithoutTutorOfferOpen: Story = {
  args: { cohort: COHORT, offerPanel },
  play: async ({ canvasElement }) => {
    const canvas = within(canvasElement);
    await expect(
      canvas.getByRole('button', {
        name: i18n.t('courses.cohorts.tutor.submit'),
      }),
    ).toBeVisible();
    await expect(
      canvas.queryByText(i18n.t('scheduling.offer.series.session')),
    ).toBeNull();
  },
};

// WCAG reflow: at 320 px the sessions table scrolls inside a region; the
// page doesn't scroll sideways.
export const WithoutTutorNarrow: Story = {
  args: { cohort: COHORT, offerPanel },
  globals: { viewport: { value: 'narrow320' } },
  play: async ({ canvasElement }) => {
    await expect(
      canvasElement.ownerDocument.documentElement.scrollWidth,
    ).toBeLessThanOrEqual(320);
  },
};

export const Ended: Story = {
  args: { cohort: ENDED_COHORT, canManage: false },
  play: async ({ canvasElement }) => {
    await expect(
      within(canvasElement).getByText(i18n.t('courses.cohorts.next.none')),
    ).toBeVisible();
  },
};

export const EditVersionConflict: Story = {
  render: () => (
    <EditCohortDialog
      open
      cohort={COHORT}
      roomCapacity={null}
      onSubmit={fn(() => Promise.resolve())}
      onCancel={fn()}
      onReload={fn()}
      initialValues={{ name: COHORT.name, max: '15' }}
      initialFailure={
        new ApiError({
          code: 'version_conflict',
          status: 409,
          requestId: 'req-story-cohort',
        })
      }
    />
  ),
  play: async () => {
    const dialog = within(await screen.findByRole('dialog'));
    await expect(
      dialog.getByText(i18n.t('courses.cohorts.edit.versionConflict')),
    ).toBeVisible();
  },
};

function familySessions(items: typeof FAMILY_SESSIONS) {
  return (
    <CohortSessionsSection
      windowStart="2031-01-04"
      state={{ status: 'ready', items }}
      timeZone={ZONE}
      linkSessions={false}
      myScheduleLinks
      onPrevious={fn()}
      onNext={fn()}
    />
  );
}

const ONE_CHILD = FAMILY_SESSIONS.map((item) => ({
  ...item,
  students: item.students!.slice(0, 1),
}));

export const Student: Story = {
  args: {
    cohort: FAMILY_COHORT,
    canManage: false,
    linkTemplate: false,
    linkSessions: false,
    myScheduleLinks: true,
    children: familySessions(ONE_CHILD),
  },
  play: async ({ canvasElement }) => {
    const canvas = within(canvasElement);
    await expect(
      canvas.getByRole('heading', {
        name: i18n.t('courses.cohorts.syllabus.heading'),
      }),
    ).toBeVisible();
    await expect(canvas.queryAllByRole('button')).toHaveLength(2);
    await expect(
      canvas.queryByRole('columnheader', {
        name: i18n.t('courses.cohorts.sessions.students'),
      }),
    ).toBeNull();
    await expect(
      canvas.getAllByText(i18n.t('mySchedule.states.skipped_closure'))[0],
    ).toBeVisible();
  },
};

export const StudentNarrow: Story = {
  ...Student,
  globals: { viewport: { value: 'narrow320' } },
  play: async ({ canvasElement }) => {
    await expect(canvasElement.scrollWidth).toBeLessThanOrEqual(
      canvasElement.clientWidth,
    );
  },
};

export const GuardianOfTwo: Story = {
  args: {
    ...Student.args,
    children: familySessions(FAMILY_SESSIONS),
  },
  play: async ({ canvasElement }) => {
    const canvas = within(canvasElement);
    await expect(
      canvas.getByRole('columnheader', {
        name: i18n.t('courses.cohorts.sessions.students'),
      }),
    ).toBeVisible();
    await expect(canvas.getByText('Ana López, Bruno Díaz')).toBeVisible();
  },
};

export const Tutor: Story = {
  args: {
    ...Student.args,
    cohort: TUTOR_COHORT,
    children: (
      <>
        {familySessions([])}
        <TutorRosterSection roster={TUTOR_COHORT.tutor_roster ?? []} />
      </>
    ),
  },
  play: async ({ canvasElement }) => {
    const canvas = within(canvasElement);
    const students = canvas.getByRole('list', {
      name: i18n.t('groups.tutorRoster.heading'),
    });
    await expect(within(students).getAllByRole('listitem')).toHaveLength(2);
    await expect(
      canvas.getByText(i18n.t('courses.cohorts.sessions.empty')),
    ).toBeVisible();
  },
};

export const TutorNarrow: Story = {
  ...Tutor,
  globals: { viewport: { value: 'narrow320' } },
  play: async ({ canvasElement }) => {
    await expect(canvasElement.scrollWidth).toBeLessThanOrEqual(
      canvasElement.clientWidth,
    );
  },
};
