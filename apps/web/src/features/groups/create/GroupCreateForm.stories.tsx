import type { Meta, StoryObj } from '@storybook/react-vite';
import { expect, fn, screen, userEvent, waitFor, within } from 'storybook/test';
import { i18n } from '@/i18n';
import { StoryQueryClient } from '@/test/StoryQueryClient';
import {
  GENERAL_SUBJECT,
  SUBJECTS,
  TUTORS,
} from '@/features/scheduling/create/fixtures';
import {
  CREATE_ROOM_CONFLICT,
  CREATE_SOFT_CONFLICTS,
  CREATED_GROUP,
  ROOMS,
  STUDENT_ANA,
  STUDENT_CARLA,
  SUBJECT_ID,
} from '../fixtures';
import { EMPTY_GROUP } from '../rules';
import { GroupCreateForm } from './GroupCreateForm';

// "New group" (#234 G2): empty, filled (the weekly rule and two student
// chips), the per-date 409 (the whole group first; one hard conflict, so no
// override), the soft-only 409 with "Override and create", and a 422 on a
// student's chip. No request: `onSubmit` and the student search are stubs.

const FILLED = {
  ...EMPTY_GROUP,
  name: 'Álgebra sábado',
  subjectId: SUBJECT_ID,
  max: '2',
  tutorId: TUTORS[0]!.id,
  classroomId: ROOMS[1]!.id,
  startsOn: '2031-01-04',
  start: '10:00',
  end: '11:30',
  students: [
    { id: STUDENT_ANA.id, name: STUDENT_ANA.display_name },
    { id: STUDENT_CARLA.id, name: STUDENT_CARLA.display_name },
  ],
};

const WEEKLY = {
  weekdays: [6],
  interval: 1,
  ends: 'count' as const,
  until: '',
  count: '4',
};

const meta = {
  title: 'Groups/GroupCreateForm',
  component: GroupCreateForm,
  args: {
    subjects: SUBJECTS,
    tutors: TUTORS,
    classrooms: ROOMS,
    searchStudents: fn(() => Promise.resolve({ items: [], total: 0 })),
    timeZone: 'America/Mexico_City',
    canOverride: true,
    labelledBy: 'group-title',
    onSubmit: fn(() => Promise.resolve(CREATED_GROUP)),
    onCreated: fn(),
  },
  render: (args) => (
    <StoryQueryClient>
      <main className="p-4">
        <h1 id="group-title" className="mb-4 text-2xl font-semibold">
          {i18n.t('groups.create.title')}
        </h1>
        <GroupCreateForm {...args} />
      </main>
    </StoryQueryClient>
  ),
} satisfies Meta<typeof GroupCreateForm>;

export default meta;
type Story = StoryObj<typeof meta>;

export const Empty: Story = {};

/** HF-12 (#597): a new group starts with the built-in General subject. */
export const GeneralPreselected: Story = {
  args: {
    subjects: [GENERAL_SUBJECT, ...SUBJECTS],
    initialValues: { ...EMPTY_GROUP, subjectId: GENERAL_SUBJECT.id },
  },
  play: async ({ canvas }) => {
    await expect(
      canvas.getByLabelText(i18n.t('scheduling.fields.subject')),
    ).toHaveValue(GENERAL_SUBJECT.id);
  },
};

export const Filled: Story = {
  args: { initialValues: FILLED, initialRecurrence: WEEKLY },
  play: async ({ canvasElement }) => {
    await expect(
      within(canvasElement).getByRole('button', {
        name: i18n.t('groups.create.remove', {
          name: STUDENT_CARLA.display_name,
        }),
      }),
    ).toBeVisible();
  },
};

export const PerDateConflicts: Story = {
  args: {
    initialValues: FILLED,
    initialRecurrence: WEEKLY,
    initialConflicts: [...CREATE_SOFT_CONFLICTS, CREATE_ROOM_CONFLICT],
  },
  play: async ({ canvasElement }) => {
    const canvas = within(canvasElement);
    await expect(
      canvas.getByText(i18n.t('groups.conflicts.wholeGroup')),
    ).toBeVisible();
    await expect(
      canvas.queryByRole('button', {
        name: i18n.t('groups.create.overrideAction'),
      }),
    ).toBeNull();
  },
};

export const SoftConflicts: Story = {
  args: {
    initialValues: FILLED,
    initialRecurrence: WEEKLY,
    initialConflicts: CREATE_SOFT_CONFLICTS,
  },
  play: async ({ canvasElement }) => {
    await expect(
      within(canvasElement).getByRole('button', {
        name: i18n.t('groups.create.overrideAction'),
      }),
    ).toBeVisible();
  },
};

export const StudentChipError: Story = {
  args: {
    initialValues: FILLED,
    initialRecurrence: WEEKLY,
    initialStudentErrors: { 1: 'errors:student_inactive' },
  },
  play: async ({ canvasElement }) => {
    await expect(
      within(canvasElement).getByText(i18n.t('errors:student_inactive')),
    ).toBeVisible();
  },
};

/**
 * #600 AC2, AC5, AC11: typed key by key in a real browser (930 → 09:30-
 * 10:30), an unreadable start after a submit, then the End list open (axe
 * scans it with the error shown), scrolled to the chosen time.
 */
export const TimeErrorAndListOpen: Story = {
  args: { initialValues: { ...FILLED, start: '', end: '' } },
  play: async ({ canvasElement }) => {
    const canvas = within(canvasElement);
    const start = canvas.getByRole('combobox', {
      name: i18n.t('scheduling.fields.start'),
    });
    const end = canvas.getByRole('combobox', {
      name: i18n.t('scheduling.fields.end'),
    });
    await userEvent.type(start, '930');
    await userEvent.tab();
    await expect(start).toHaveValue('09:30');
    await expect(end).toHaveValue('10:30');

    await userEvent.clear(start);
    await userEvent.type(start, 'abc');
    await userEvent.tab();
    await userEvent.click(
      canvas.getByRole('button', { name: i18n.t('groups.create.submit') }),
    );
    await expect(
      canvas.getByText(i18n.t('scheduling.validation.timeFormat')),
    ).toBeVisible();
    await expect(start).toHaveAttribute('aria-invalid', 'true');

    await userEvent.click(end);
    const list = await screen.findByRole('listbox', {
      name: i18n.t('scheduling.fields.end'),
    });
    const chosen = within(list).getByRole('option', { selected: true });
    await expect(chosen).toHaveTextContent('10:30');
    await waitFor(() =>
      expect(list.scrollTop).toBeGreaterThanOrEqual(chosen.offsetTop - 1),
    );
  },
};
