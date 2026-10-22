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
  CREATED_WITH_WARNINGS,
  FILLED,
  NO_SUGGESTIONS,
  NO_TUTOR_SUGGESTIONS,
  OVERRIDDEN,
  OVERRIDE_REASON,
  SOFT_CONFLICTS,
  SUBJECTS,
  SUGGESTIONS,
  TUTOR_SUGGESTIONS,
  TUTORS,
  UNASSIGNED,
} from './fixtures';
import type { SessionOverride } from './rules';
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
// warnings. Suggested tutors (#51 G3): loading, three results (one
// preferring the room, one with limits), empty and an error. No request:
// `onSubmit`, `onSuggest`, `onSuggestTutors` and the student search are
// stubs. Repeats weekly (#54b E3): the section on, a 409 grouped by date
// (no override for series yet) and the series success panel.

const meta = {
  title: 'Scheduling/ScheduleSessionForm',
  component: ScheduleSessionForm,
  args: {
    subjects: SUBJECTS,
    tutors: TUTORS,
    classrooms: CLASSROOMS,
    searchStudents: fn(() => Promise.resolve([ANA])),
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

// --- Suggested tutors (#51 G3) -----------------------------------------------

const suggestTutorsName = () => i18n.t('scheduling.tutorSuggestions.button');
const tutorsHeading = () => i18n.t('scheduling.tutorSuggestions.heading');

function withTutors(answer: () => Promise<typeof TUTOR_SUGGESTIONS>) {
  return { initialValues: FILLED, onSuggestTutors: fn(answer) };
}

export const TutorSuggestionsLoading: Story = {
  args: withTutors(() => new Promise<never>(() => {})),
  play: async ({ canvas, userEvent }) => {
    await userEvent.click(
      canvas.getByRole('button', { name: suggestTutorsName() }),
    );
    await expect(
      await canvas.findByText(i18n.t('scheduling.tutorSuggestions.loading')),
    ).toBeVisible();
  },
};

export const TutorSuggestionsResults: Story = {
  args: withTutors(() => Promise.resolve(TUTOR_SUGGESTIONS)),
  play: async ({ canvas, userEvent }) => {
    await userEvent.click(
      canvas.getByRole('button', { name: suggestTutorsName() }),
    );
    const section = await canvas.findByRole('region', {
      name: tutorsHeading(),
    });
    const list = within(section).getByRole('list');
    await expect(within(list).getAllByRole('listitem')).toHaveLength(3);
    await expect(section).toHaveTextContent(
      i18n.t('scheduling.tutorSuggestions.prefersRoom'),
    );
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
    await waitFor(() =>
      expect(
        canvas.getByLabelText(i18n.t('scheduling.fields.tutor')),
      ).toHaveFocus(),
    );
  },
};

export const TutorSuggestionsEmpty: Story = {
  args: withTutors(() => Promise.resolve(NO_TUTOR_SUGGESTIONS)),
  play: async ({ canvas, userEvent }) => {
    await userEvent.click(
      canvas.getByRole('button', { name: suggestTutorsName() }),
    );
    await expect(
      await canvas.findByText(i18n.t('scheduling.tutorSuggestions.empty')),
    ).toBeVisible();
  },
};

export const TutorSuggestionsError: Story = {
  args: withTutors(() =>
    Promise.reject(
      new ApiError({ code: 'internal_error', status: 500, requestId: 'req-x' }),
    ),
  ),
  play: async ({ canvas, userEvent }) => {
    await userEvent.click(
      canvas.getByRole('button', { name: suggestTutorsName() }),
    );
    const section = await canvas.findByRole('region', {
      name: tutorsHeading(),
    });
    await expect(await within(section).findByRole('alert')).toBeVisible();
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
