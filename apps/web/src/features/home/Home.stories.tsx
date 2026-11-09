import type { Meta, StoryObj } from '@storybook/react-vite';
import {
  QueryClient,
  QueryClientProvider,
  type QueryKey,
} from '@tanstack/react-query';
import { useState, type ReactNode } from 'react';
import { expect, within } from 'storybook/test';
import { ApiError } from '@/app/api';
import type { Session, SessionUserBody } from '@/features/auth/session';
import { calendarQueryKey } from '@/features/calendar/api';
import {
  OWN_FAMILY_KEY,
  portalKeys,
} from '@/features/family-portal/billing/api';
import { MY_SCHEDULE_KEY } from '@/features/my-schedule/api';
import { TEACHING } from '@/features/my-schedule/whose';
import { OVERDUE_CLOSEOUTS_KEY } from '@/features/scheduling/overdue/api';
import { i18n } from '@/i18n';
import { StoryAuth } from '@/test/StoryAuth';
import { StoryRouter } from '@/test/StoryRouter';
import { homeKeys } from './api';
import {
  BUSY_TODAY,
  FAMILY_ID,
  FAMILY_TODAY,
  FINANCE,
  GUARDIAN,
  KID_A,
  KID_B,
  NO_ROLE,
  NOW,
  OWN_FAMILY,
  OWNER,
  SELF_STUDENT,
  STUDENT,
  SUMMARY,
  TODAY,
  TUTOR,
  TUTOR_TODAY,
  calendarPage,
  homeworkSummary,
  offsetPage,
} from './fixtures';
import { RoleHomePage } from './RoleHomePage';
import { personalWindow, staffWindow } from './rules';
import { TodayPanel } from './TodayPanel';

// Home (HF-06 F3): the staff "Today" panel (Owner busy, Finance), the role
// home (a tutor with pending items, a guardian with two children and a
// balance, a student with nothing, an account with no role), one section in
// error, and the narrow layout. Every query is seeded: no request. axe
// runs on each story in both themes.

const ZONE = 'America/Mexico_City';
const STAFF = staffWindow(TODAY, ZONE);
const PERSONAL = personalWindow(TODAY, ZONE);
const range = (window: typeof STAFF) => ({ from: window.from, to: window.to });
const count = (value: number) => ({ count: value, more: false });

const signedIn = (user: SessionUserBody): Session => ({
  status: 'signedIn',
  user,
});

type Seed = ReadonlyArray<readonly [QueryKey, unknown]>;

const STAFF_SEED: Seed = [
  [calendarQueryKey(range(STAFF)), calendarPage(BUSY_TODAY)],
  [homeKeys.approvals, count(3)],
  [[...OVERDUE_CLOSEOUTS_KEY, 'count'], offsetPage(2)],
  [homeKeys.overdueAccounts, count(4)],
];

const TUTOR_SEED: Seed = [
  [
    [...MY_SCHEDULE_KEY, 'schedule', TEACHING, range(PERSONAL)],
    {
      entries: TUTOR_TODAY.map((item) => ({ audience: 'tutor', item })),
      truncated: false,
    },
  ],
  [homeKeys.offers, count(2)],
  [homeKeys.toGrade, { count: 50, more: true }],
];

const familyEntries = (items: typeof FAMILY_TODAY) => ({
  entries: items.map((item) => ({ audience: 'family', item })),
  truncated: false,
});

const OTHER_KID = {
  ...KID_A,
  student_id: '00000000-0000-4000-8000-000000549c09',
  display_name: 'Tomás Pérez',
};

const GUARDIAN_SEED: Seed = [
  [
    [...MY_SCHEDULE_KEY, 'family-students'],
    { items: [KID_A, OTHER_KID, KID_B] },
  ],
  [
    [...MY_SCHEDULE_KEY, 'schedule', KID_A.student_id, range(PERSONAL)],
    familyEntries(FAMILY_TODAY),
  ],
  [
    [...MY_SCHEDULE_KEY, 'schedule', OTHER_KID.student_id, range(PERSONAL)],
    familyEntries(FAMILY_TODAY.slice(1)),
  ],
  [homeKeys.studentHomework(KID_A.student_id), homeworkSummary(2, 1).summary],
  [
    homeKeys.studentHomework(OTHER_KID.student_id),
    homeworkSummary(1, 0).summary,
  ],
  [OWN_FAMILY_KEY, OWN_FAMILY],
  [portalKeys.summary(FAMILY_ID), SUMMARY],
];

const STUDENT_SEED: Seed = [
  [[...MY_SCHEDULE_KEY, 'family-students'], { items: [SELF_STUDENT] }],
  [
    [...MY_SCHEDULE_KEY, 'schedule', SELF_STUDENT.student_id, range(PERSONAL)],
    familyEntries([]),
  ],
  [
    homeKeys.studentHomework(SELF_STUDENT.student_id),
    homeworkSummary(0, 0).summary,
  ],
];

/** A query client with the seeded data and, optionally, failed keys. */
function Client({
  seed,
  failed = [],
  children,
}: {
  seed: Seed;
  failed?: readonly QueryKey[];
  children: ReactNode;
}) {
  const [client] = useState(() => {
    const queryClient = new QueryClient({
      defaultOptions: {
        queries: { retry: false, retryOnMount: false, staleTime: Infinity },
      },
    });
    for (const [key, value] of seed) {
      queryClient.setQueryData(key, value);
    }
    for (const key of failed) {
      queryClient
        .getQueryCache()
        .build(queryClient, { queryKey: key })
        .setState({
          status: 'error',
          error: new ApiError({ status: 503, code: 'database_unavailable' }),
          fetchStatus: 'idle',
          errorUpdatedAt: Date.now(),
        });
    }
    return queryClient;
  });
  return <QueryClientProvider client={client}>{children}</QueryClientProvider>;
}

function Page({
  user,
  seed,
  failed,
  staff = false,
}: {
  user: SessionUserBody;
  seed: Seed;
  failed?: readonly QueryKey[];
  staff?: boolean;
}) {
  return (
    <StoryAuth session={signedIn(user)}>
      <Client seed={seed} failed={failed}>
        <StoryRouter
          path="/"
          routes={[
            {
              path: '*',
              element: (
                <main>
                  {staff ? (
                    <TodayPanel user={user} now={NOW} />
                  ) : (
                    <RoleHomePage now={NOW} />
                  )}
                </main>
              ),
            },
          ]}
        />
      </Client>
    </StoryAuth>
  );
}

const meta = {
  title: 'Home/Home',
  render: () => <></>,
} satisfies Meta;
export default meta;
type Story = StoryObj<typeof meta>;

export const OwnerBusy: Story = {
  name: 'Owner busy (Today panel)',
  render: () => <Page user={OWNER} seed={STAFF_SEED} staff />,
  play: async ({ canvas }) => {
    await expect(
      await canvas.findByRole('heading', {
        level: 2,
        name: i18n.t('home.today.title'),
      }),
    ).toBeVisible();
    await expect(
      canvas.getByRole('link', {
        name: i18n.t('home.pending.approvals', { count: 3, shown: '3' }),
      }),
    ).toHaveAttribute('href', '/approvals');
    await expect(
      canvas.getByText(i18n.t('home.more', { count: 2, shown: '2' })),
    ).toBeVisible();
  },
};

export const Finance: Story = {
  name: 'Finance (Today panel)',
  render: () => (
    <Page
      user={FINANCE}
      seed={[
        [calendarQueryKey(range(STAFF)), calendarPage([])],
        [homeKeys.overdueAccounts, count(4)],
      ]}
      staff
    />
  ),
  play: async ({ canvas }) => {
    await expect(
      await canvas.findByText(i18n.t('home.staff.empty')),
    ).toBeVisible();
    await expect(
      canvas.queryByRole('link', {
        name: i18n.t('home.pending.approvals', { count: 3, shown: '3' }),
      }),
    ).toBeNull();
  },
};

export const TutorWithPending: Story = {
  name: 'Tutor with pending items',
  render: () => <Page user={TUTOR} seed={TUTOR_SEED} />,
  play: async ({ canvas }) => {
    await expect(
      await canvas.findByRole('heading', {
        level: 1,
        name: i18n.t('home.greeting', { name: TUTOR.display_name }),
      }),
    ).toBeVisible();
    await expect(
      canvas.getByRole('link', {
        name: i18n.t('home.pending.toGrade', { count: 50, shown: '50+' }),
      }),
    ).toBeVisible();
  },
};

export const GuardianTwoChildren: Story = {
  name: 'Guardian with two children and a balance',
  render: () => <Page user={GUARDIAN} seed={GUARDIAN_SEED} />,
  play: async ({ canvas }) => {
    await expect(
      await canvas.findByText(
        i18n.t('home.family.consent', { name: KID_B.display_name }),
      ),
    ).toBeVisible();
    await expect(
      canvas.getAllByText(
        new RegExp(i18n.t('home.forStudent', { name: OTHER_KID.display_name })),
      ).length,
    ).toBeGreaterThan(0);
  },
};

export const StudentEmpty: Story = {
  name: 'Student with nothing',
  render: () => <Page user={STUDENT} seed={STUDENT_SEED} />,
  play: async ({ canvas }) => {
    await expect(
      await canvas.findByText(i18n.t('home.personal.empty')),
    ).toBeVisible();
    await expect(
      canvas.getByText(i18n.t('home.pending.nothing')),
    ).toBeVisible();
  },
};

export const NoRole: Story = {
  name: 'No role (fallback)',
  render: () => <Page user={NO_ROLE} seed={[]} />,
  play: async ({ canvas }) => {
    await expect(
      await canvas.findByText(i18n.t('home.fallback.none')),
    ).toBeVisible();
  },
};

export const SectionError: Story = {
  name: 'One section in error',
  render: () => (
    <Page
      user={TUTOR}
      seed={TUTOR_SEED.slice(1)}
      failed={[[...MY_SCHEDULE_KEY, 'schedule', TEACHING, range(PERSONAL)]]}
    />
  ),
  play: async ({ canvas }) => {
    const alert = await canvas.findByRole('alert');
    await expect(
      within(alert).getByRole('button', { name: i18n.t('feedback.retry') }),
    ).toBeVisible();
    await expect(
      canvas.getByRole('link', {
        name: i18n.t('home.pending.offers', { count: 2, shown: '2' }),
      }),
    ).toBeVisible();
  },
};

/** D4: at 320 px the home reflows with no horizontal scroll. */
export const Narrow: Story = {
  name: 'Narrow (320 px)',
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
  globals: { viewport: { value: 'narrow320' } },
  render: () => <Page user={GUARDIAN} seed={GUARDIAN_SEED} />,
  play: async ({ canvasElement, canvas }) => {
    const view = canvasElement.ownerDocument.defaultView!;
    await expect(view.innerWidth).toBe(320);
    await canvas.findByText(
      i18n.t('home.family.consent', { name: KID_B.display_name }),
    );
    const root = canvasElement.ownerDocument.documentElement;
    await expect(root.scrollWidth).toBeLessThanOrEqual(root.clientWidth);
  },
};
