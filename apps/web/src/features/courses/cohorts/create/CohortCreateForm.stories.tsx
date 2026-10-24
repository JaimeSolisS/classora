import type { Meta, StoryObj } from '@storybook/react-vite';
import { expect, fn, screen, within } from 'storybook/test';
import { TUTORS } from '@/features/scheduling/create/fixtures';
import { i18n } from '@/i18n';
import { StoryQueryClient } from '@/test/StoryQueryClient';
import { ALGEBRA } from '../../fixtures';
import {
  CREATED_COHORT,
  HARD_CONFLICT,
  SOFT_CONFLICTS,
  TEMPLATE_OPTIONS,
} from '../fixtures';
import { EMPTY_COHORT } from '../rules';
import { CohortCreateForm } from './CohortCreateForm';

// "New cohort" (#243 E3): empty, filled (a template, a tutor with "Respond
// by", the weekly rule and its summary), field errors (#65 codes), the
// date-grouped 409 with a hard conflict (no override), and the soft-only
// 409 with the override dialog open. No request: `onSubmit` is a stub.

const ROOMS = [
  { id: '01900000-0000-7000-8000-00000000e021', name: 'Aula 2', capacity: 8 },
];

const FILLED = {
  ...EMPTY_COHORT,
  name: 'Álgebra enero',
  templateId: ALGEBRA.id,
  subjectId: ALGEBRA.subjects[0]!.id,
  max: '12',
  classroomId: ROOMS[0]!.id,
  tutorId: TUTORS[0]!.id,
  respondByDate: '2031-01-03',
  respondByTime: '12:00',
  startsOn: '2031-01-06',
  start: '10:00',
};

const WEEKLY = {
  weekdays: [1, 3],
  interval: 1,
  ends: 'never' as const,
  until: '',
  count: '',
};

const meta = {
  title: 'Courses/CohortCreateForm',
  component: CohortCreateForm,
  args: {
    templates: TEMPLATE_OPTIONS,
    tutors: TUTORS,
    classrooms: ROOMS,
    timeZone: 'America/Mexico_City',
    canOverride: true,
    labelledBy: 'cohort-title',
    onSubmit: fn(() => Promise.resolve(CREATED_COHORT)),
    onCreated: fn(),
  },
  render: (args) => (
    <StoryQueryClient>
      <main className="p-4">
        <h1 id="cohort-title" className="mb-4 text-2xl font-semibold">
          {i18n.t('courses.cohorts.create.title')}
        </h1>
        <CohortCreateForm {...args} />
      </main>
    </StoryQueryClient>
  ),
} satisfies Meta<typeof CohortCreateForm>;

export default meta;
type Story = StoryObj<typeof meta>;

export const Empty: Story = {
  play: async ({ canvasElement }) => {
    await expect(
      within(canvasElement).queryByText(i18n.t('scheduling.series.ends')),
    ).toBeNull();
  },
};

export const Filled: Story = {
  args: { initialValues: FILLED, initialRecurrence: WEEKLY },
  play: async ({ canvasElement }) => {
    const canvas = within(canvasElement);
    await expect(
      canvas.getByText(
        i18n.t('courses.cohorts.create.templateSessions', {
          count: 10,
          minutes: 90,
        }),
      ),
    ).toBeVisible();
    await expect(
      canvas.getByText(i18n.t('courses.cohorts.create.tutorHelp')),
    ).toBeVisible();
  },
};

export const FieldErrors: Story = {
  args: {
    initialValues: FILLED,
    initialRecurrence: WEEKLY,
    initialErrors: {
      name: 'errors:cohort_name_taken',
      template: 'errors:template_inactive',
      startsOn: 'errors:starts_on_in_past',
    },
    initialRecurrenceErrors: { weekdays: 'errors:too_many_occurrences' },
  },
  play: async ({ canvasElement }) => {
    await expect(
      within(canvasElement).getByText(i18n.t('errors:cohort_name_taken')),
    ).toBeVisible();
  },
};

export const PerDateConflicts: Story = {
  args: {
    initialValues: FILLED,
    initialRecurrence: WEEKLY,
    initialConflicts: [...SOFT_CONFLICTS, HARD_CONFLICT],
  },
  play: async ({ canvasElement }) => {
    await expect(
      within(canvasElement).queryByRole('button', {
        name: i18n.t('courses.cohorts.create.overrideAction'),
      }),
    ).toBeNull();
  },
};

export const OverrideDialog: Story = {
  args: {
    initialValues: FILLED,
    initialRecurrence: WEEKLY,
    initialConflicts: SOFT_CONFLICTS,
  },
  play: async ({ canvasElement }) => {
    within(canvasElement)
      .getByRole('button', {
        name: i18n.t('courses.cohorts.create.overrideAction'),
      })
      .click();
    await expect(await screen.findByRole('dialog')).toBeVisible();
  },
};
