import type { Meta, StoryObj } from '@storybook/react-vite';
import { expect, fn, screen, waitFor, within } from 'storybook/test';
import { ApiError } from '@/app/api';
import { i18n } from '@/i18n';
import { StoryQueryClient } from '@/test/StoryQueryClient';
import {
  ANA,
  CLASSROOMS,
  CONFLICTS,
  CREATED,
  CARMEN_AVAILABILITY,
  COHORT_OPTION,
  COHORT_SUMMARY,
  CREATED_WITH_WARNINGS,
  EMPTY_GROUP_SUMMARY,
  FILLED,
  GROUP_CONFLICTS,
  GROUP_OPTION,
  GROUP_SUMMARY,
  OWNER_FILLED,
  NO_QUALIFIED_TUTORS,
  NO_SUGGESTIONS,
  NO_TUTOR_SUGGESTIONS,
  OVERRIDDEN,
  OVERRIDE_REASON,
  SOFT_CONFLICTS,
  SUBJECTS,
  SUGGESTIONS,
  TUTOR_SUGGESTIONS,
  TUTOR_SUGGESTIONS_WITH_WARNINGS,
  TUTORS,
  UNASSIGNED,
} from './fixtures';
import type { OwnerSummary, SessionOverride } from './rules';
import type { ScheduleOwners } from './ScheduleSessionForm';
import {
  SERIES_CONFLICTS,
  SERIES_CREATED,
  SOFT_SERIES_CONFLICTS,
} from '../series/fixtures';
import { ScheduleSessionForm } from './ScheduleSessionForm';

// "Schedule a session" (#48 F4, #49 F5): empty, filled, submitting, the
// conflict panel (one hard, two soft), the success panel and the 422 field
// errors; the override action (soft only with the button, mixed without it,
// the non-holder message), the override dialog (empty, a reason error,
// submitting) and the success panel with an override. Suggested times
// after a conflict (#50 C3): loading, with slots (one with a workload
// warning), empty and an error; the success panel with all three workload
// warnings. Suggested tutors (#51 G3, HF-05b E3), loaded by themselves:
// loading, three results (one preferring the room, one within its limits,
// the chosen one), with warnings, empty with counts, nobody qualified (with
// the Tutors link) and an error; the chosen tutor's availability and free
// times. No request:
// `onSubmit`, `onSuggest`, `onSuggestTutors` and the student search are
// stubs. Repeats weekly (#54b E3): the section on, a 409 grouped by date
// (no override for series yet) and the series success panel. Group and
// course sessions (HF-11b): a group chosen with its roster, a cohort with
// its dates, an empty roster, and a group's student-overlap conflict.

const meta = {
  title: 'Scheduling/ScheduleSessionForm',
  component: ScheduleSessionForm,
  args: {
    subjects: SUBJECTS,
    tutors: TUTORS,
    classrooms: CLASSROOMS,
    searchStudents: fn(() => Promise.resolve({ items: [ANA], total: 1 })),
    timeZone: 'America/Mexico_City',
    labelledBy: 'schedule-title',
    onSubmit: fn(() => Promise.resolve(CREATED)),
    onSubmitSeries: fn(() => Promise.resolve(SERIES_CREATED)),
    canOverride: true,
  },
  render: (args) => (
    <StoryQueryClient>
      <main className="p-4">
        <h1 id="schedule-title" className="mb-4 text-2xl font-semibold">
          {i18n.t('scheduling.title')}
        </h1>
        <ScheduleSessionForm {...args} />
      </main>
    </StoryQueryClient>
  ),
} satisfies Meta<typeof ScheduleSessionForm>;

export default meta;
type Story = StoryObj<typeof meta>;

const submitName = () => i18n.t('scheduling.submit');

function conflictError(conflicts = CONFLICTS): ApiError {
  return new ApiError({
    code: 'scheduling_conflict',
    status: 409,
    requestId: 'req-story-409',
    problem: {
      type: 'about:blank',
      title: 'Conflict',
      status: 409,
      code: 'scheduling_conflict',
      request_id: 'req-story-409',
      conflicts,
    },
  });
}

/** 409 with the soft conflicts, then `withOverride` once an override is sent. */
function softThen(withOverride: () => Promise<typeof OVERRIDDEN>) {
  return fn((_values: unknown, override?: SessionOverride) =>
    override ? withOverride() : Promise.reject(conflictError(SOFT_CONFLICTS)),
  );
}

const overrideName = () => i18n.t('scheduling.override.action');

export const Empty: Story = {
  play: async ({ canvas }) => {
    await expect(
      canvas.getByLabelText(i18n.t('scheduling.fields.start')),
    ).toHaveAccessibleDescription(
      i18n.t('scheduling.zone', { zone: 'America/Mexico_City' }),
    );
    await expect(
      canvas.getByRole('option', { name: i18n.t('scheduling.noTutor') }),
    ).toBeInTheDocument();
  },
};

export const Filled: Story = {
  args: { initialValues: FILLED },
  play: async ({ canvas }) => {
    await expect(
      canvas.getByLabelText(i18n.t('scheduling.fields.date')),
    ).toHaveValue('2031-01-06');
  },
};

export const Submitting: Story = {
  args: {
    initialValues: FILLED,
    onSubmit: fn(() => new Promise<never>(() => {})),
  },
  play: async ({ canvas, userEvent }) => {
    await userEvent.click(canvas.getByRole('button', { name: submitName() }));
    await expect(
      await canvas.findByRole('button', {
        name: i18n.t('scheduling.submitting'),
      }),
    ).toBeDisabled();
  },
};

export const Conflicts: Story = {
  args: {
    initialValues: FILLED,
    onSubmit: fn(() =>
      Promise.reject(
        new ApiError({
          code: 'scheduling_conflict',
          status: 409,
          requestId: 'req-story-409',
          problem: {
            type: 'about:blank',
            title: 'Conflict',
            status: 409,
            code: 'scheduling_conflict',
            request_id: 'req-story-409',
            conflicts: CONFLICTS,
          },
        }),
      ),
    ),
  },
  play: async ({ canvas, userEvent }) => {
    await userEvent.click(canvas.getByRole('button', { name: submitName() }));
    const alert = await canvas.findByRole('alert');
    await expect(alert).toHaveTextContent(i18n.t('scheduling.severity.hard'));
    await waitFor(() => expect(alert.querySelector('h2')).toHaveFocus());
  },
};

export const Success: Story = {
  args: { initialValues: FILLED },
  play: async ({ canvas, userEvent }) => {
    await userEvent.click(canvas.getByRole('button', { name: submitName() }));
    await expect(
      await canvas.findByText(
        i18n.t('scheduling.success.offered', { tutor: 'Carmen Ruiz' }),
      ),
    ).toBeVisible();
  },
};

export const SuccessUnassigned: Story = {
  args: {
    initialValues: { ...FILLED, tutorId: '', classroomId: '' },
    onSubmit: fn(() => Promise.resolve(UNASSIGNED)),
  },
  play: async ({ canvas, userEvent }) => {
    await userEvent.click(canvas.getByRole('button', { name: submitName() }));
    await expect(
      await canvas.findByText(i18n.t('scheduling.success.unassigned')),
    ).toBeVisible();
  },
};

export const FieldErrors: Story = {
  args: {
    initialValues: FILLED,
    onSubmit: fn(() =>
      Promise.reject(
        new ApiError({
          code: 'validation_error',
          status: 422,
          requestId: 'req-story-422',
          fieldErrors: [
            {
              path: ['body', 'classroom_id'],
              code: 'classroom_inactive',
              message: 'x',
            },
            {
              path: ['body', 'ends_at'],
              code: 'duration_out_of_range',
              message: 'x',
            },
          ],
        }),
      ),
    ),
  },
  play: async ({ canvas, userEvent }) => {
    await userEvent.click(canvas.getByRole('button', { name: submitName() }));
    await expect(
      await canvas.findByText(i18n.t('errors:duration_out_of_range')),
    ).toBeVisible();
    await expect(
      canvas.getByLabelText(i18n.t('scheduling.fields.end')),
    ).toHaveAttribute('aria-invalid', 'true');
  },
};

export const SoftConflictsCanOverride: Story = {
  args: {
    initialValues: FILLED,
    onSubmit: softThen(() => Promise.resolve(OVERRIDDEN)),
  },
  play: async ({ canvas, userEvent }) => {
    await userEvent.click(canvas.getByRole('button', { name: submitName() }));
    const alert = await canvas.findByRole('alert');
    await expect(alert).toHaveTextContent(i18n.t('scheduling.severity.soft'));
    await expect(
      canvas.getByRole('button', { name: overrideName() }),
    ).toBeVisible();
  },
};

export const MixedConflictsNoOverride: Story = {
  args: {
    initialValues: FILLED,
    onSubmit: fn(() => Promise.reject(conflictError())),
  },
  play: async ({ canvas, userEvent }) => {
    await userEvent.click(canvas.getByRole('button', { name: submitName() }));
    await expect(
      await canvas.findByText(i18n.t('scheduling.override.fixHardFirst')),
    ).toBeVisible();
    await expect(
      canvas.queryByRole('button', { name: overrideName() }),
    ).toBeNull();
  },
};

export const NotAllowedToOverride: Story = {
  args: {
    initialValues: FILLED,
    canOverride: false,
    onSubmit: softThen(() => Promise.resolve(OVERRIDDEN)),
  },
  play: async ({ canvas, userEvent }) => {
    await userEvent.click(canvas.getByRole('button', { name: submitName() }));
    await expect(
      await canvas.findByText(i18n.t('scheduling.override.notAllowed')),
    ).toBeVisible();
    await expect(
      canvas.queryByRole('button', { name: overrideName() }),
    ).toBeNull();
  },
};

export const OverrideDialog: Story = {
  args: {
    initialValues: FILLED,
    onSubmit: softThen(() => Promise.resolve(OVERRIDDEN)),
  },
  play: async ({ canvas, userEvent }) => {
    await userEvent.click(canvas.getByRole('button', { name: submitName() }));
    await userEvent.click(
      await canvas.findByRole('button', { name: overrideName() }),
    );
    const dialog = within(await screen.findByRole('dialog'));
    await expect(dialog.getAllByRole('listitem')).toHaveLength(2);
    await expect(
      dialog.getByLabelText(i18n.t('scheduling.override.reason')),
    ).toHaveValue('');
  },
};

export const OverrideReasonError: Story = {
  args: {
    initialValues: FILLED,
    onSubmit: softThen(() => Promise.resolve(OVERRIDDEN)),
  },
  play: async ({ canvas, userEvent }) => {
    await userEvent.click(canvas.getByRole('button', { name: submitName() }));
    await userEvent.click(
      await canvas.findByRole('button', { name: overrideName() }),
    );
    const dialog = within(await screen.findByRole('dialog'));
    const reason = dialog.getByLabelText(i18n.t('scheduling.override.reason'));
    await userEvent.type(reason, '   ');
    await userEvent.click(
      dialog.getByRole('button', {
        name: i18n.t('scheduling.override.submit'),
      }),
    );
    await expect(reason).toHaveAttribute('aria-invalid', 'true');
    await expect(reason).toHaveAccessibleDescription(
      expect.stringContaining(i18n.t('scheduling.override.reasonRequired')),
    );
  },
};

export const OverrideSubmitting: Story = {
  args: {
    initialValues: FILLED,
    onSubmit: softThen(() => new Promise<never>(() => {})),
  },
  play: async ({ canvas, userEvent }) => {
    await userEvent.click(canvas.getByRole('button', { name: submitName() }));
    await userEvent.click(
      await canvas.findByRole('button', { name: overrideName() }),
    );
    const dialog = within(await screen.findByRole('dialog'));
    await userEvent.type(
      dialog.getByLabelText(i18n.t('scheduling.override.reason')),
      OVERRIDE_REASON,
    );
    await userEvent.click(
      dialog.getByRole('button', {
        name: i18n.t('scheduling.override.submit'),
      }),
    );
    await expect(
      await dialog.findByRole('button', {
        name: i18n.t('scheduling.override.submitting'),
      }),
    ).toBeDisabled();
  },
};

export const SuccessWithOverride: Story = {
  args: {
    initialValues: FILLED,
    onSubmit: softThen(() => Promise.resolve(OVERRIDDEN)),
  },
  play: async ({ canvas, userEvent }) => {
    await userEvent.click(canvas.getByRole('button', { name: submitName() }));
    await userEvent.click(
      await canvas.findByRole('button', { name: overrideName() }),
    );
    const dialog = within(await screen.findByRole('dialog'));
    await userEvent.type(
      dialog.getByLabelText(i18n.t('scheduling.override.reason')),
      OVERRIDE_REASON,
    );
    await userEvent.click(
      dialog.getByRole('button', {
        name: i18n.t('scheduling.override.submit'),
      }),
    );
    await expect(
      await canvas.findByText(i18n.t('scheduling.override.scheduledWith')),
    ).toBeVisible();
    await waitFor(() =>
      expect(
        canvas.getByRole('heading', {
          name: i18n.t('scheduling.success.heading'),
        }),
      ).toHaveFocus(),
    );
  },
};

// --- #50: suggested times and workload warnings --------------------------------

const suggestionsHeading = () => i18n.t('scheduling.suggestions.heading');

/** The create always conflicts; `onSuggest` answers as given. */
function conflictThenSuggest(onSuggest: () => Promise<typeof SUGGESTIONS>) {
  return {
    initialValues: FILLED,
    onSubmit: fn(() => Promise.reject(conflictError())),
    onSuggest: fn(onSuggest),
  };
}

export const SuggestionsLoading: Story = {
  args: conflictThenSuggest(() => new Promise<never>(() => {})),
  play: async ({ canvas, userEvent }) => {
    await userEvent.click(canvas.getByRole('button', { name: submitName() }));
    await expect(
      await canvas.findByText(i18n.t('scheduling.suggestions.loading')),
    ).toBeVisible();
  },
};

export const SuggestionsWithSlots: Story = {
  args: conflictThenSuggest(() => Promise.resolve(SUGGESTIONS)),
  play: async ({ canvas, userEvent }) => {
    await userEvent.click(canvas.getByRole('button', { name: submitName() }));
    const section = await canvas.findByRole('region', {
      name: suggestionsHeading(),
    });
    const slots = await within(section).findAllByRole('button');
    await expect(slots).toHaveLength(3);
    await expect(section).toHaveTextContent(i18n.t('scheduling.warnings.note'));
    await userEvent.click(slots[0]!);
    await expect(
      canvas.getByLabelText(i18n.t('scheduling.fields.start')),
    ).toHaveValue('11:00');
    await waitFor(() =>
      expect(canvas.getByRole('button', { name: submitName() })).toHaveFocus(),
    );
  },
};

export const SuggestionsWithWarning: Story = {
  args: conflictThenSuggest(() =>
    Promise.resolve({ ...SUGGESTIONS, slots: SUGGESTIONS.slots.slice(1, 2) }),
  ),
  play: async ({ canvas, userEvent }) => {
    await userEvent.click(canvas.getByRole('button', { name: submitName() }));
    const section = await canvas.findByRole('region', {
      name: suggestionsHeading(),
    });
    await expect(
      await within(section).findByText(i18n.t('scheduling.warnings.note')),
    ).toBeVisible();
  },
};

export const SuggestionsEmpty: Story = {
  args: conflictThenSuggest(() => Promise.resolve(NO_SUGGESTIONS)),
  play: async ({ canvas, userEvent }) => {
    await userEvent.click(canvas.getByRole('button', { name: submitName() }));
    const section = await canvas.findByRole('region', {
      name: suggestionsHeading(),
    });
    await expect(
      await within(section).findByRole('status'),
    ).toBeInTheDocument();
  },
};

export const SuggestionsError: Story = {
  args: conflictThenSuggest(() =>
    Promise.reject(
      new ApiError({ code: 'internal_error', status: 500, requestId: 'req-x' }),
    ),
  ),
  play: async ({ canvas, userEvent }) => {
    await userEvent.click(canvas.getByRole('button', { name: submitName() }));
    const section = await canvas.findByRole('region', {
      name: suggestionsHeading(),
    });
    await expect(await within(section).findByRole('alert')).toBeVisible();
  },
};

export const SuccessWithWarnings: Story = {
  args: {
    initialValues: FILLED,
    onSubmit: fn(() => Promise.resolve(CREATED_WITH_WARNINGS)),
  },
  play: async ({ canvas, userEvent }) => {
    await userEvent.click(canvas.getByRole('button', { name: submitName() }));
    await expect(
      await canvas.findByText(i18n.t('scheduling.warnings.heading')),
    ).toBeVisible();
    await expect(
      canvas.getByText(
        i18n.t('scheduling.warnings.break_too_short', {
          count: 1,
          required: 15,
        }),
      ),
    ).toBeVisible();
  },
};

// --- Suggested tutors (#51 G3, HF-05b E3) ------------------------------------
// The panel loads by itself once the slot is valid (FILLED is), after the
// 400 ms wait.

const tutorsHeading = () => i18n.t('scheduling.tutorSuggestions.heading');

function withTutors(answer: () => Promise<typeof TUTOR_SUGGESTIONS>) {
  return { initialValues: FILLED, onSuggestTutors: fn(answer) };
}

export const TutorSuggestionsLoading: Story = {
  args: withTutors(() => new Promise<never>(() => {})),
  play: async ({ canvas }) => {
    await expect(
      await canvas.findByText(i18n.t('scheduling.tutorSuggestions.loading')),
    ).toBeVisible();
  },
};

export const TutorSuggestionsResults: Story = {
  args: withTutors(() => Promise.resolve(TUTOR_SUGGESTIONS)),
  play: async ({ canvas, userEvent }) => {
    const section = await canvas.findByRole(
      'region',
      { name: tutorsHeading() },
      { timeout: 3000 },
    );
    const list = await within(section).findByRole('list');
    await expect(within(list).getAllByRole('listitem')).toHaveLength(3);
    await expect(section).toHaveTextContent(
      i18n.t('scheduling.tutorSuggestions.prefersRoom'),
    );
    await expect(section).toHaveTextContent(
      i18n.t('scheduling.tutorSuggestions.withinLimits'),
    );
    // FILLED's tutor is Carmen Ruiz: her row is chosen.
    await expect(
      within(section).getByRole('button', {
        name: i18n.t('scheduling.tutorSuggestions.chosenTutor', {
          name: 'Carmen Ruiz',
        }),
      }),
    ).toHaveAttribute('aria-pressed', 'true');
    await userEvent.click(
      within(section).getByRole('button', {
        name: i18n.t('scheduling.tutorSuggestions.chooseTutor', {
          name: 'Diego Torres',
        }),
      }),
    );
    await expect(
      canvas.getByLabelText(i18n.t('scheduling.fields.tutor')),
    ).toHaveValue(TUTORS[1]!.id);
    await expect(
      within(section).getByRole('button', {
        name: i18n.t('scheduling.tutorSuggestions.chosenTutor', {
          name: 'Diego Torres',
        }),
      }),
    ).toHaveAttribute('aria-pressed', 'true');
  },
};

export const TutorSuggestionsWithWarnings: Story = {
  args: withTutors(() => Promise.resolve(TUTOR_SUGGESTIONS_WITH_WARNINGS)),
  play: async ({ canvas }) => {
    const section = await canvas.findByRole(
      'region',
      { name: tutorsHeading() },
      { timeout: 3000 },
    );
    await waitFor(() =>
      expect(section).toHaveTextContent(
        i18n.t('scheduling.warnings.break_too_short', {
          count: 5,
          required: 10,
        }),
      ),
    );
  },
};

export const TutorSuggestionsEmptyWithCounts: Story = {
  args: withTutors(() => Promise.resolve(NO_TUTOR_SUGGESTIONS)),
  play: async ({ canvas }) => {
    await expect(
      await canvas.findByText(
        new RegExp(
          i18n.t('scheduling.tutorSuggestions.busyPart', { count: 2 }),
        ),
        undefined,
        { timeout: 3000 },
      ),
    ).toBeVisible();
  },
};

export const TutorSuggestionsNoneQualified: Story = {
  args: {
    ...withTutors(() => Promise.resolve(NO_QUALIFIED_TUTORS)),
    assignSubjectsLink: (
      <a href="#tutors" className="underline underline-offset-4">
        {i18n.t('scheduling.tutorSuggestions.assignSubjects')}
      </a>
    ),
  },
  play: async ({ canvas }) => {
    await expect(
      await canvas.findByRole(
        'link',
        { name: i18n.t('scheduling.tutorSuggestions.assignSubjects') },
        { timeout: 3000 },
      ),
    ).toBeVisible();
  },
};

export const TutorSuggestionsError: Story = {
  args: withTutors(() =>
    Promise.reject(
      new ApiError({ code: 'internal_error', status: 500, requestId: 'req-x' }),
    ),
  ),
  play: async ({ canvas }) => {
    const section = await canvas.findByRole(
      'region',
      { name: tutorsHeading() },
      { timeout: 3000 },
    );
    await expect(
      await within(section).findByRole('alert', undefined, { timeout: 3000 }),
    ).toBeVisible();
  },
};

// --- The chosen tutor (HF-05b C, E3) ------------------------------------------

export const TutorAvailabilityAndFreeTimes: Story = {
  args: {
    ...withTutors(() => Promise.resolve(TUTOR_SUGGESTIONS)),
    loadTutorAvailability: fn(() => Promise.resolve(CARMEN_AVAILABILITY)),
    onFindFreeTimes: fn(() => Promise.resolve(SUGGESTIONS)),
  },
  play: async ({ canvas, userEvent }) => {
    await expect(
      await canvas.findByText(
        i18n.t('scheduling.tutorAvailability.exceptions'),
      ),
    ).toBeVisible();
    await userEvent.click(
      canvas.getByRole('button', {
        name: i18n.t('scheduling.freeTimes.button'),
      }),
    );
    await expect(
      await canvas.findByRole('region', {
        name: i18n.t('scheduling.freeTimes.heading', { name: 'Carmen Ruiz' }),
      }),
    ).toBeVisible();
  },
};

// --- Repeats weekly (#54b E3) -------------------------------------------------

const REPEATS = {
  initialValues: { ...FILLED, tutorId: '', classroomId: '' },
  initialRepeats: true,
  initialRecurrence: {
    weekdays: [1, 3],
    interval: 1,
    ends: 'count' as const,
    until: '',
    count: '6',
  },
};
const seriesSubmitName = () => i18n.t('scheduling.series.submit');

export const RepeatsWeekly: Story = {
  args: REPEATS,
  play: async ({ canvas }) => {
    await expect(
      canvas.getByLabelText(i18n.t('scheduling.series.startsOn')),
    ).toHaveValue('2031-01-06');
    await expect(
      canvas.getByRole('group', { name: i18n.t('scheduling.series.weekdays') }),
    ).toBeInTheDocument();
    await expect(
      canvas.getByLabelText(i18n.t('scheduling.series.count')),
    ).toHaveValue(6);
  },
};

export const RepeatsConflicts: Story = {
  args: {
    ...REPEATS,
    onSubmitSeries: fn(() => Promise.reject(conflictError(SERIES_CONFLICTS))),
  },
  play: async ({ canvas, userEvent }) => {
    await userEvent.click(
      canvas.getByRole('button', { name: seriesSubmitName() }),
    );
    const alert = await canvas.findByRole('alert');
    await expect(alert).toHaveTextContent(i18n.t('scheduling.severity.soft'));
    await expect(alert.querySelectorAll('h3')).toHaveLength(2);
    await waitFor(() => expect(alert.querySelector('h2')).toHaveFocus());
    await expect(
      canvas.getByText(i18n.t('scheduling.override.fixHardFirst')),
    ).toBeVisible();
  },
};

// #236: only soft conflicts, so the Owner can override them all.
export const RepeatsOverride: Story = {
  args: {
    ...REPEATS,
    onSubmitSeries: fn(
      (_values: unknown, _recurrence: unknown, override?: unknown) =>
        override
          ? Promise.resolve({
              ...SERIES_CREATED,
              occurrences: SERIES_CREATED.occurrences.map((o) => ({
                ...o,
                overridden: o.state !== 'skipped_closure',
              })),
            })
          : Promise.reject(conflictError(SOFT_SERIES_CONFLICTS)),
    ),
  },
  play: async ({ canvas, userEvent }) => {
    await userEvent.click(
      canvas.getByRole('button', { name: seriesSubmitName() }),
    );
    await userEvent.click(
      await canvas.findByRole('button', {
        name: i18n.t('scheduling.override.action'),
      }),
    );
    const dialog = within(await screen.findByRole('dialog'));
    await userEvent.type(
      dialog.getByLabelText(i18n.t('scheduling.override.reason')),
      'Domingos acordados',
    );
    await userEvent.click(
      dialog.getByRole('button', {
        name: i18n.t('scheduling.override.submit'),
      }),
    );
    await expect(
      await canvas.findByText(
        i18n.t('scheduling.series.override.scheduledWith', { count: 2 }),
      ),
    ).toBeVisible();
  },
};

export const RepeatsSuccess: Story = {
  args: REPEATS,
  play: async ({ canvas, userEvent }) => {
    await userEvent.click(
      canvas.getByRole('button', { name: seriesSubmitName() }),
    );
    await expect(
      await canvas.findByText(i18n.t('scheduling.series.success.heading')),
    ).toBeVisible();
  },
};

// --- Group and course sessions (HF-11b) ---------------------------------------

function owners(
  summary: OwnerSummary,
  onSubmit: ScheduleOwners['onSubmit'] = fn(() => Promise.resolve(CREATED)),
): ScheduleOwners {
  return {
    searchGroups: fn(() =>
      Promise.resolve({ items: [GROUP_OPTION], total: 1 }),
    ),
    searchCohorts: fn(() =>
      Promise.resolve({ items: [COHORT_OPTION], total: 1 }),
    ),
    loadOwner: fn(() => Promise.resolve(summary)),
    onSubmit,
    classroomCapacities: { [CLASSROOMS[0]!.id]: 1 },
  };
}

export const GroupChosen: Story = {
  args: {
    initialValues: { ...OWNER_FILLED, classroomId: CLASSROOMS[0]!.id },
    initialKind: 'group',
    initialOwner: GROUP_OPTION,
    owners: owners(GROUP_SUMMARY),
  },
  play: async ({ canvas }) => {
    await expect(
      canvas.getByRole('radio', {
        name: i18n.t('scheduling.create.type.group'),
      }),
    ).toBeChecked();
    await expect(
      await canvas.findByText(
        i18n.t('scheduling.create.owner.enrolled', { count: 2 }),
      ),
    ).toBeVisible();
    await expect(
      canvas.queryByLabelText(i18n.t('scheduling.fields.student')),
    ).toBeNull();
    // The room is smaller than the roster: a warning, not a block.
    await expect(
      canvas.getByLabelText(i18n.t('scheduling.fields.classroom')),
    ).toHaveAccessibleDescription(
      i18n.t('scheduling.create.owner.roomTooSmall', {
        count: 2,
        capacity: 1,
      }),
    );
  },
};

export const CourseChosen: Story = {
  args: {
    initialValues: OWNER_FILLED,
    initialKind: 'course',
    initialOwner: COHORT_OPTION,
    owners: owners(COHORT_SUMMARY),
  },
  play: async ({ canvas }) => {
    await expect(
      await canvas.findByText(i18n.t('scheduling.create.owner.dates')),
    ).toBeVisible();
    await waitFor(() =>
      expect(
        canvas.getByLabelText(i18n.t('scheduling.fields.date')),
      ).toHaveAccessibleDescription(/2031/),
    );
  },
};

export const EmptyRoster: Story = {
  args: {
    initialValues: OWNER_FILLED,
    initialKind: 'group',
    initialOwner: GROUP_OPTION,
    owners: owners(EMPTY_GROUP_SUMMARY),
  },
  play: async ({ canvas }) => {
    await expect(
      await canvas.findByText(i18n.t('scheduling.create.owner.emptyRoster')),
    ).toBeVisible();
  },
};

export const GroupStudentOverlap: Story = {
  args: {
    initialValues: OWNER_FILLED,
    initialKind: 'group',
    initialOwner: GROUP_OPTION,
    owners: owners(
      GROUP_SUMMARY,
      fn(() => Promise.reject(conflictError(GROUP_CONFLICTS))),
    ),
  },
  play: async ({ canvas, userEvent }) => {
    await canvas.findByText(
      i18n.t('scheduling.create.owner.enrolled', { count: 2 }),
    );
    await userEvent.click(canvas.getByRole('button', { name: submitName() }));
    const alert = await canvas.findByRole('alert');
    await expect(alert).toHaveTextContent(
      i18n.t('scheduling.create.owner.studentOverlap', {
        name: GROUP_SUMMARY.students[1]!.name,
      }),
    );
    await waitFor(() => expect(alert.querySelector('h2')).toHaveFocus());
  },
};
