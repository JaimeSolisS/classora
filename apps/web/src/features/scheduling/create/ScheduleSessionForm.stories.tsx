import type { Meta, StoryObj } from '@storybook/react-vite';
import { expect, fn, waitFor } from 'storybook/test';
import { ApiError } from '@/app/api';
import { i18n } from '@/i18n';
import { StoryQueryClient } from '@/test/StoryQueryClient';
import {
  ANA,
  CLASSROOMS,
  CONFLICTS,
  CREATED,
  FILLED,
  SUBJECTS,
  TUTORS,
  UNASSIGNED,
} from './fixtures';
import { ScheduleSessionForm } from './ScheduleSessionForm';

// "Schedule a session" (#48 F4): empty, filled, submitting, the conflict
// panel (one hard, two soft), the success panel and the 422 field errors.
// No request: `onSubmit` and the student search are stubs.

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
