import type { Meta, StoryObj } from '@storybook/react-vite';
import { MemoryRouter } from 'react-router';
import { expect, fn, within } from 'storybook/test';
import { ApiError } from '@/app/api';
import { i18n } from '@/i18n';
import { StoryQueryClient } from '@/test/StoryQueryClient';
import { MAKEUP_AVAILABLE, MAKEUP_SESSION } from '../session/fixtures';
import { ANA, CLASSROOMS, CREATED, SUBJECTS, TUTORS } from './fixtures';
import { MakeupTargetNotice, MakeupUnusableNotice } from './MakeupNotices';
import { MAKEUP_LINK_CLASS, makeupValues } from './rules';
import { ScheduleSessionForm } from './ScheduleSessionForm';

// Makeup mode of "Schedule a session" (#436 D2): the form with the fixed
// student and subject and the deadline, a refused target with the link to
// the live makeup, and the notice in place of the form. No request. Axe runs
// in light and dark.

const ZONE = 'America/Mexico_City';

const meta = {
  title: 'Scheduling/MakeupMode',
  component: ScheduleSessionForm,
  args: {
    subjects: SUBJECTS,
    tutors: TUTORS,
    classrooms: CLASSROOMS,
    searchStudents: fn(() => Promise.resolve([ANA])),
    timeZone: ZONE,
    labelledBy: 'schedule-title',
    onSubmit: fn(() => Promise.resolve(CREATED)),
    canOverride: true,
    initialValues: makeupValues(MAKEUP_AVAILABLE),
    makeup: {
      studentName: MAKEUP_AVAILABLE.participants[0]!.display_name,
      subjectName: MAKEUP_AVAILABLE.subject.name,
      notice: <MakeupTargetNotice target={MAKEUP_AVAILABLE} timeZone={ZONE} />,
      targetLink: (
        <a href="#target" className={MAKEUP_LINK_CLASS}>
          {i18n.t('scheduling.makeup.openCancelled')}
        </a>
      ),
      takenLink: () => (
        <a href="#makeup" className={MAKEUP_LINK_CLASS}>
          {i18n.t('scheduling.makeup.openMakeup')}
        </a>
      ),
      onScheduleAnother: fn(),
    },
  },
  render: (args) => (
    <StoryQueryClient>
      <MemoryRouter>
        <main className="p-4">
          <h1 id="schedule-title" className="mb-4 text-2xl font-semibold">
            {i18n.t('scheduling.title')}
          </h1>
          <ScheduleSessionForm {...args} />
        </main>
      </MemoryRouter>
    </StoryQueryClient>
  ),
} satisfies Meta<typeof ScheduleSessionForm>;

export default meta;
type Story = StoryObj<typeof meta>;

const FILLED = {
  ...makeupValues(MAKEUP_AVAILABLE),
  date: '2031-01-06',
  start: '10:00',
  end: '11:00',
};

export const Form: Story = {
  play: async ({ canvasElement }) => {
    const canvas = within(canvasElement);
    await expect(
      canvas.getByText(MAKEUP_AVAILABLE.subject.name),
    ).toBeInTheDocument();
    await expect(canvas.queryByRole('switch')).not.toBeInTheDocument();
  },
};

export const Taken: Story = {
  args: {
    initialValues: FILLED,
    onSubmit: fn(() =>
      Promise.reject(
        new ApiError({
          code: 'makeup_already_scheduled',
          status: 409,
          requestId: 'req-story-409',
          problem: {
            type: 'about:blank',
            title: 'Conflict',
            status: 409,
            code: 'makeup_already_scheduled',
            request_id: 'req-story-409',
            details: { class_session_id: MAKEUP_SESSION.id },
          },
        }),
      ),
    ),
  },
  play: async ({ canvasElement, userEvent }) => {
    const canvas = within(canvasElement);
    await userEvent.click(
      canvas.getByRole('button', { name: i18n.t('scheduling.submit') }),
    );
    await expect(
      await canvas.findByRole('link', {
        name: i18n.t('scheduling.makeup.openMakeup'),
      }),
    ).toBeInTheDocument();
  },
};

export const NotUsable: Story = {
  render: () => (
    <MemoryRouter>
      <main className="p-4">
        <h1 className="mb-4 text-2xl font-semibold">
          {i18n.t('scheduling.title')}
        </h1>
        <MakeupUnusableNotice problem="windowPassed" />
      </main>
    </MemoryRouter>
  ),
  play: async ({ canvasElement }) => {
    await expect(
      within(canvasElement).getByRole('link', {
        name: i18n.t('scheduling.makeup.plainForm'),
      }),
    ).toBeInTheDocument();
  },
};
