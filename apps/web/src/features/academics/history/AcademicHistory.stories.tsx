import type { Meta, StoryObj } from '@storybook/react-vite';
import { useState } from 'react';
import { expect, within } from 'storybook/test';
import { ApiError } from '@/app/api';
import type { Session } from '@/features/auth/session';
import { JOSE_LIMITED } from '@/features/students/fixtures';
import { studentDetailKey } from '@/features/students/api';
import { i18n } from '@/i18n';
import type { LocalDate } from '@/lib/time';
import { StoryAuth } from '@/test/StoryAuth';
import { StoryQueryClient } from '@/test/StoryQueryClient';
import { StoryRouter } from '@/test/StoryRouter';
import { AcademicHistoryView, type Audience } from './AcademicHistory';
import { FAMILY_STUDENTS_KEY, historyKey, workingKey } from './api';
import {
  CHILD_ID,
  EMPTY,
  FAMILY_STUDENTS,
  GROUPED,
  STUDENT_ID,
  TRUNCATED,
  WORKING_ITEMS,
} from './fixtures';
import type { HistoryState } from './historyState';
import { MyHistoryPage } from './MyHistoryPage';
import { StudentHistoryPage } from './StudentHistoryPage';
import { subjectsOf } from './historyState';
import type { HistoryControls } from './urlState';

// The academic history (#308 G2): grouped (periods, "No period", several
// subjects and authors, an updated entry, a course entry, a long unbroken
// feedback), chronological, empty, empty with filters, truncated, consent
// required, loading and error; the family page with its picker and the
// staff and tutor page with both tabs (query data seeded: no request).
// The phone views have 320 and 390 px variants that assert the page never
// scrolls sideways. Axe runs in light and dark (the storybook projects).

const VIEWPORTS = {
  narrow320: {
    name: 'Narrow (320 px)',
    styles: { width: '320px', height: '640px' },
    type: 'mobile',
  },
  phone390: {
    name: 'Phone (390 px)',
    styles: { width: '390px', height: '844px' },
    type: 'mobile',
  },
} as const;

const DEFAULT: HistoryControls = {
  group: 'school_year',
  view: 'grouped',
  subject: null,
  from: null,
  to: null,
};

interface ViewArgs {
  state: HistoryState;
  initial?: HistoryControls;
  audience?: Audience;
}

function ViewStory({ state, initial = DEFAULT, audience = 'staff' }: ViewArgs) {
  const [controls, setControls] = useState(initial);
  return (
    <main className="flex min-w-0 flex-col gap-6 p-4">
      <h1 className="text-2xl font-semibold">
        {i18n.t('academics.history.title')}
      </h1>
      <AcademicHistoryView
        state={state}
        controls={controls}
        onControls={setControls}
        subjects={subjectsOf(GROUPED)}
        audience={audience}
        studentName="Mateo Pérez"
      />
    </main>
  );
}

const meta = {
  title: 'Academics/AcademicHistory',
  component: ViewStory,
  args: { state: { status: 'ready', history: GROUPED } },
  parameters: { viewport: { options: VIEWPORTS } },
  render: (args) => (
    <StoryQueryClient>
      <StoryRouter routes={[{ path: '/', element: <ViewStory {...args} /> }]} />
    </StoryQueryClient>
  ),
} satisfies Meta<typeof ViewStory>;

export default meta;
type Story = StoryObj<typeof meta>;

async function noSideScroll(canvasElement: HTMLElement) {
  const page = canvasElement.ownerDocument.documentElement;
  await expect(page.scrollWidth).toBeLessThanOrEqual(page.clientWidth);
}

const phone = (story: Story, value: 'narrow320' | 'phone390'): Story => ({
  ...story,
  globals: { viewport: { value } },
  play: async (context) => {
    await story.play?.(context);
    await noSideScroll(context.canvasElement);
  },
});

export const Grouped: Story = {
  play: async ({ canvasElement }) => {
    const canvas = within(canvasElement);
    await expect(
      canvas.getByRole('heading', {
        level: 2,
        name: i18n.t('academics.history.noPeriod'),
      }),
    ).toBeVisible();
    await expect(canvas.getAllByRole('article')).toHaveLength(4);
    await expect(
      canvas.getAllByText(i18n.t('academics.history.entry.updated')).length,
    ).toBeGreaterThan(0);
  },
};
export const GroupedNarrow320 = phone(Grouped, 'narrow320');
export const GroupedPhone390 = phone(Grouped, 'phone390');

export const Chronological: Story = {
  args: { initial: { ...DEFAULT, view: 'list' } },
  play: async ({ canvasElement }) => {
    await expect(
      within(canvasElement).getByRole('button', {
        name: i18n.t('academics.history.views.list'),
      }),
    ).toHaveAttribute('aria-pressed', 'true');
  },
};
export const ChronologicalNarrow320 = phone(Chronological, 'narrow320');

export const Family: Story = {
  args: { audience: 'family' },
  play: async ({ canvasElement }) => {
    await expect(
      within(canvasElement).queryAllByRole('link', {
        name: /./,
      }),
    ).toHaveLength(0);
  },
};

export const Empty: Story = {
  args: { state: { status: 'ready', history: EMPTY } },
  play: async ({ canvasElement }) => {
    await expect(
      within(canvasElement).getByText(i18n.t('academics.history.empty')),
    ).toBeVisible();
  },
};

export const EmptyFiltered: Story = {
  args: {
    state: { status: 'ready', history: EMPTY },
    initial: { ...DEFAULT, from: '2020-01-01' as LocalDate },
  },
  play: async ({ canvasElement }) => {
    await expect(
      within(canvasElement).getByText(
        i18n.t('academics.history.emptyFiltered'),
      ),
    ).toBeVisible();
  },
};
export const EmptyFilteredNarrow320 = phone(EmptyFiltered, 'narrow320');

export const Truncated: Story = {
  args: { state: { status: 'ready', history: TRUNCATED } },
  play: async ({ canvasElement }) => {
    await expect(
      within(canvasElement).getByText(i18n.t('academics.history.truncated')),
    ).toBeVisible();
  },
};

export const RangeInvalid: Story = {
  args: {
    state: { status: 'invalid' },
    initial: {
      ...DEFAULT,
      from: '2026-12-01' as LocalDate,
      to: '2026-11-01' as LocalDate,
    },
  },
  play: async ({ canvasElement }) => {
    await expect(
      within(canvasElement).getByText(i18n.t('academics.history.rangeInvalid')),
    ).toBeVisible();
  },
};

export const ConsentRequired: Story = {
  args: { state: { status: 'consent' }, audience: 'family' },
  play: async ({ canvasElement }) => {
    await expect(
      within(canvasElement).getByRole('heading', {
        name: i18n.t('academics.history.consentTitle'),
      }),
    ).toBeVisible();
  },
};
export const ConsentRequiredNarrow320 = phone(ConsentRequired, 'narrow320');

export const Unavailable: Story = {
  args: { state: { status: 'missing' } },
  play: async ({ canvasElement }) => {
    await expect(
      within(canvasElement).getByText(i18n.t('academics.history.unavailable')),
    ).toBeVisible();
  },
};

export const Loading: Story = {
  args: { state: { status: 'loading' } },
};

export const Failed: Story = {
  args: {
    state: {
      status: 'error',
      error: new ApiError({ code: 'internal_error', status: 500 }),
      onRetry: () => undefined,
    },
  },
  play: async ({ canvasElement }) => {
    await expect(within(canvasElement).getByRole('alert')).toBeVisible();
  },
};

const QUERY = {
  group: 'school_year',
  subject: null,
  from: null,
  to: null,
} as const;

/** The family page, its queries seeded. */
export const FamilyPage: Story = {
  render: () => (
    <StoryQueryClient
      data={[
        [FAMILY_STUDENTS_KEY, FAMILY_STUDENTS],
        [historyKey(CHILD_ID, QUERY), GROUPED],
      ]}
    >
      <StoryRouter
        routes={[
          {
            path: '/',
            element: (
              <main className="min-w-0 p-4">
                <MyHistoryPage />
              </main>
            ),
          },
        ]}
      />
    </StoryQueryClient>
  ),
  play: async ({ canvasElement }) => {
    await expect(
      within(canvasElement).getByLabelText(i18n.t('academics.history.whose')),
    ).toBeVisible();
  },
};
export const FamilyPageNarrow320 = phone(FamilyPage, 'narrow320');
export const FamilyPagePhone390 = phone(FamilyPage, 'phone390');

const tutorSession: Session = {
  status: 'signedIn',
  user: {
    id: '00000000-0000-4000-8000-000000000001',
    username: 'demo.tutor',
    display_name: 'Carmen Ruiz',
    roles: ['tutor'],
    permissions: ['student.view', 'tutor.view'],
    privacy_notice_required: false,
    preferences: { language: 'es', theme: 'system' },
    center_time_zone: 'America/Mexico_City',
  },
};

function staffPage(path: string) {
  return function StaffPageStory() {
    return (
      <StoryQueryClient
        data={[
          [studentDetailKey(STUDENT_ID), JOSE_LIMITED],
          [historyKey(STUDENT_ID, QUERY), GROUPED],
          [
            workingKey(STUDENT_ID, null),
            {
              pages: [{ items: WORKING_ITEMS, next_cursor: 'next' }],
              pageParams: [null],
            },
          ],
        ]}
      >
        <StoryAuth session={tutorSession}>
          <StoryRouter
            path={path}
            routes={[
              {
                path: '/students/:studentId/history',
                element: (
                  <main className="min-w-0 p-4">
                    <StudentHistoryPage />
                  </main>
                ),
              },
            ]}
          />
        </StoryAuth>
      </StoryQueryClient>
    );
  };
}

/** The staff and tutor page: the History tab. */
export const StudentPageHistory: Story = {
  render: staffPage(`/students/${STUDENT_ID}/history`),
  play: async ({ canvasElement }) => {
    await expect(
      within(canvasElement).getByRole('tab', {
        name: i18n.t('academics.history.tabs.history'),
      }),
    ).toHaveAttribute('aria-selected', 'true');
  },
};
export const StudentPageHistoryNarrow320 = phone(
  StudentPageHistory,
  'narrow320',
);

/** The staff and tutor page: the Evaluations tab (the working list). */
export const StudentPageEvaluations: Story = {
  render: staffPage(`/students/${STUDENT_ID}/history?tab=evaluations`),
  play: async ({ canvasElement }) => {
    const canvas = within(canvasElement);
    await expect(
      canvas.getByRole('tab', {
        name: i18n.t('academics.history.tabs.evaluations'),
      }),
    ).toHaveAttribute('aria-selected', 'true');
    await expect(
      canvas.getByRole('button', {
        name: i18n.t('academics.history.working.loadMore'),
      }),
    ).toBeVisible();
  },
};
export const StudentPageEvaluationsNarrow320 = phone(
  StudentPageEvaluations,
  'narrow320',
);
