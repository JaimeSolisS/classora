import type { Meta, StoryObj } from '@storybook/react-vite';
import type { QueryKey } from '@tanstack/react-query';
import { expect, within } from 'storybook/test';
import { ApiError } from '@/app/api';
import { FAMILY_STUDENTS_KEY } from '@/features/academics/history/api';
import {
  ADULT_ID,
  CHILD_ID,
  FAMILY_STUDENTS,
} from '@/features/academics/history/fixtures';
import type { Session } from '@/features/auth/session';
import { studentDetailKey } from '@/features/students/api';
import { JOSE_LIMITED } from '@/features/students/fixtures';
import { i18n } from '@/i18n';
import { StoryAuth } from '@/test/StoryAuth';
import { StoryQueryClient } from '@/test/StoryQueryClient';
import { StoryRouter } from '@/test/StoryRouter';
import { planListKey, type LearningPlanList } from './api';
import {
  EMPTY_LIST,
  FAMILY_LIST,
  STUDENT_ID,
  TRUNCATED_LIST,
  TUTOR_LIST,
} from './fixtures';
import { LearningPlansListPage } from './LearningPlansListPage';
import { MyLearningPlansPage } from './MyLearningPlansPage';
import { PlanList, type PlanListState } from './PlanList';

// The plan lists (#310 G2): the staff and tutor page (mixed statuses with
// "New plan", truncated, empty), the family page (the picker, an adult who
// hasn't shared, empty) and the list's loading and error states. Their
// queries are seeded, so no request leaves the story. Every story has 320
// and 390 px variants that assert the page never scrolls sideways, plus
// 1280 px for the staff list. Axe runs in light and dark.

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
  desktop1280: {
    name: 'Desktop (1280 px)',
    styles: { width: '1280px', height: '800px' },
    type: 'desktop',
  },
} as const;

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

const guardianSession: Session = {
  ...tutorSession,
  user: {
    ...tutorSession.user,
    username: 'demo.guardian',
    display_name: 'Laura Pérez',
    roles: ['guardian'],
    permissions: ['student.view', 'guardian.view'],
  },
};

function staffPage(list: LearningPlanList) {
  return function StaffListStory() {
    return (
      <StoryQueryClient
        data={[
          [studentDetailKey(STUDENT_ID), JOSE_LIMITED],
          [planListKey(STUDENT_ID), list],
        ]}
      >
        <StoryAuth session={tutorSession}>
          <StoryRouter
            path={`/students/${STUDENT_ID}/learning-plans`}
            routes={[
              {
                path: '/students/:studentId/learning-plans',
                element: (
                  <main className="min-w-0 p-4">
                    <LearningPlansListPage />
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

function familyPage(
  path: string,
  data: ReadonlyArray<readonly [QueryKey, unknown]>,
) {
  return function FamilyListStory() {
    return (
      <StoryQueryClient data={data}>
        <StoryAuth session={guardianSession}>
          <StoryRouter
            path={path}
            routes={[
              {
                path: '/my-learning-plans',
                element: (
                  <main className="min-w-0 p-4">
                    <MyLearningPlansPage />
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

function StateStory({ state }: { state: PlanListState }) {
  return (
    <StoryQueryClient>
      <StoryRouter
        routes={[
          {
            path: '/',
            element: (
              <main className="min-w-0 p-4">
                <PlanList
                  state={state}
                  linkTo={(id) => `/learning-plans/${id}`}
                  emptyText={i18n.t('learningPlans.empty')}
                />
              </main>
            ),
          },
        ]}
      />
    </StoryQueryClient>
  );
}

const meta = {
  title: 'LearningPlans/Lists',
  component: StateStory,
  args: { state: { status: 'loading' } },
  parameters: { viewport: { options: VIEWPORTS } },
} satisfies Meta<typeof StateStory>;

export default meta;
type Story = StoryObj<typeof meta>;

async function noSideScroll(canvasElement: HTMLElement) {
  const page = canvasElement.ownerDocument.documentElement;
  await expect(page.scrollWidth).toBeLessThanOrEqual(page.clientWidth);
}

const at = (
  story: Story,
  value: 'narrow320' | 'phone390' | 'desktop1280',
): Story => ({
  ...story,
  globals: { viewport: { value } },
  play: async (context) => {
    await story.play?.(context);
    await noSideScroll(context.canvasElement);
  },
});

export const StaffList: Story = {
  render: staffPage(TUTOR_LIST),
  play: async ({ canvasElement }) => {
    const canvas = within(canvasElement);
    await expect(
      canvas.getByRole('link', { name: i18n.t('learningPlans.new') }),
    ).toBeVisible();
    await expect(canvas.getAllByRole('listitem')).toHaveLength(3);
  },
};
export const StaffListNarrow320 = at(StaffList, 'narrow320');
export const StaffListPhone390 = at(StaffList, 'phone390');
export const StaffListDesktop1280 = at(StaffList, 'desktop1280');

export const StaffTruncated: Story = {
  render: staffPage(TRUNCATED_LIST),
  play: async ({ canvasElement }) => {
    await expect(
      within(canvasElement).getByText(
        i18n.t('learningPlans.truncated', { max: 100 }),
      ),
    ).toBeVisible();
  },
};
export const StaffTruncatedNarrow320 = at(StaffTruncated, 'narrow320');
export const StaffTruncatedPhone390 = at(StaffTruncated, 'phone390');

export const StaffEmpty: Story = {
  render: staffPage(EMPTY_LIST),
  play: async ({ canvasElement }) => {
    await expect(
      within(canvasElement).getByText(i18n.t('learningPlans.empty')),
    ).toBeVisible();
  },
};
export const StaffEmptyNarrow320 = at(StaffEmpty, 'narrow320');
export const StaffEmptyPhone390 = at(StaffEmpty, 'phone390');

export const FamilyWithPicker: Story = {
  render: familyPage('/my-learning-plans', [
    [FAMILY_STUDENTS_KEY, FAMILY_STUDENTS],
    [planListKey(CHILD_ID), FAMILY_LIST],
  ]),
  play: async ({ canvasElement }) => {
    const canvas = within(canvasElement);
    await expect(
      canvas.getByLabelText(i18n.t('learningPlans.family.whose')),
    ).toBeVisible();
    await expect(canvas.getAllByRole('listitem')).toHaveLength(2);
  },
};
export const FamilyWithPickerNarrow320 = at(FamilyWithPicker, 'narrow320');
export const FamilyWithPickerPhone390 = at(FamilyWithPicker, 'phone390');

export const FamilyConsentRequired: Story = {
  render: familyPage(`/my-learning-plans?student=${ADULT_ID}`, [
    [FAMILY_STUDENTS_KEY, FAMILY_STUDENTS],
  ]),
  play: async ({ canvasElement }) => {
    await expect(
      within(canvasElement).getByRole('heading', {
        name: i18n.t('academics.history.consentTitle'),
      }),
    ).toBeVisible();
  },
};
export const FamilyConsentRequiredNarrow320 = at(
  FamilyConsentRequired,
  'narrow320',
);
export const FamilyConsentRequiredPhone390 = at(
  FamilyConsentRequired,
  'phone390',
);

export const FamilyEmpty: Story = {
  render: familyPage('/my-learning-plans', [
    [FAMILY_STUDENTS_KEY, FAMILY_STUDENTS],
    [planListKey(CHILD_ID), { ...FAMILY_LIST, items: [] }],
  ]),
  play: async ({ canvasElement }) => {
    await expect(
      within(canvasElement).getByText(i18n.t('learningPlans.family.empty')),
    ).toBeVisible();
  },
};
export const FamilyEmptyNarrow320 = at(FamilyEmpty, 'narrow320');
export const FamilyEmptyPhone390 = at(FamilyEmpty, 'phone390');

export const Loading: Story = {
  play: async ({ canvasElement }) => {
    await expect(
      canvasElement.querySelector('[aria-busy="true"]'),
    ).not.toBeNull();
  },
};
export const LoadingNarrow320 = at(Loading, 'narrow320');
export const LoadingPhone390 = at(Loading, 'phone390');

export const ErrorState: Story = {
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
export const ErrorStateNarrow320 = at(ErrorState, 'narrow320');
export const ErrorStatePhone390 = at(ErrorState, 'phone390');
