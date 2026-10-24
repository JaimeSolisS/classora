import type { Meta, StoryObj } from '@storybook/react-vite';
import { useState } from 'react';
import { expect, userEvent, waitFor, within } from 'storybook/test';
import { ApiError } from '@/app/api';
import { i18n } from '@/i18n';
import { StoryQueryClient } from '@/test/StoryQueryClient';
import { StoryRouter } from '@/test/StoryRouter';
import type { FamilyStudents, MyScheduleLoaders, SchedulePage } from './api';
import {
  FAMILY_ITEMS,
  familyStudents,
  LUCIA,
  MATEO,
  TUTOR_ID,
  TUTOR_ITEMS,
  WEEK,
  ZONE,
} from './fixtures';
import { MyScheduleView } from './MySchedulePage';
import type { MyScheduleState } from './urlState';

// "My schedule" (#67 G7): a tutor's List and Week; a family List with every
// family state; the child selector with a consent-required child; empty,
// loading, error and truncated; the details dialog; the 320 px layout. The
// loaders are stubs: no request leaves the story. Axe runs in light and
// dark (the Storybook projects).

interface StoryArgs {
  isTutor: boolean;
  hasFamilyRole: boolean;
  initial: MyScheduleState;
  students: () => Promise<FamilyStudents>;
  schedule: () => Promise<SchedulePage>;
}

function MyScheduleStory({
  isTutor,
  hasFamilyRole,
  initial,
  students,
  schedule,
}: StoryArgs) {
  const [state, setState] = useState(initial);
  const loaders: MyScheduleLoaders = {
    fetchFamilyStudents: () => students(),
    fetchTutorSchedule: () => schedule(),
    fetchFamilySchedule: () => schedule(),
  };
  return (
    <main className="flex min-w-0 flex-col gap-6 p-4">
      <h1 className="text-2xl font-semibold">{i18n.t('mySchedule.title')}</h1>
      <MyScheduleView
        userId={TUTOR_ID}
        isTutor={isTutor}
        hasFamilyRole={hasFamilyRole}
        timeZone={ZONE}
        today={WEEK}
        state={state}
        onStateChange={setState}
        loaders={loaders}
      />
    </main>
  );
}

function tutorPage(truncated = false): SchedulePage {
  return {
    entries: TUTOR_ITEMS.map((item) => ({ audience: 'tutor', item })),
    truncated,
  };
}

function familyPage(): SchedulePage {
  return {
    entries: FAMILY_ITEMS.map((item) => ({ audience: 'family', item })),
    truncated: false,
  };
}

const LIST: MyScheduleState = { whose: null, view: 'list', date: WEEK };

const meta = {
  title: 'My schedule/MyScheduleView',
  component: MyScheduleStory,
  args: {
    isTutor: true,
    hasFamilyRole: false,
    initial: LIST,
    students: () => Promise.resolve(familyStudents()),
    schedule: () => Promise.resolve(tutorPage()),
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
  render: (args) => (
    <StoryQueryClient>
      <StoryRouter
        routes={[{ path: '/', element: <MyScheduleStory {...args} /> }]}
      />
    </StoryQueryClient>
  ),
} satisfies Meta<typeof MyScheduleStory>;

export default meta;
type Story = StoryObj<typeof meta>;

const escape = (text: string) => text.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');

async function events(canvasElement: HTMLElement) {
  const region = await within(canvasElement).findByRole('region', {
    name: i18n.t('mySchedule.regionLabel'),
  });
  await waitFor(() => expect(region).toHaveAttribute('aria-busy', 'false'));
  return within(region);
}

export const TutorList: Story = {
  play: async ({ canvasElement }) => {
    const region = await events(canvasElement);
    await expect(
      region.getByRole('button', {
        name: new RegExp(escape(i18n.t('mySchedule.states.offered'))),
      }),
    ).toBeVisible();
    await expect(
      within(canvasElement).getByText(i18n.t('mySchedule.whose.teaching')),
    ).toBeVisible();
  },
};

export const TutorWeek: Story = {
  args: { initial: { ...LIST, view: 'week' } },
  play: async ({ canvasElement }) => {
    const region = await events(canvasElement);
    await expect(region.getAllByRole('button')).toHaveLength(3);
  },
};

export const FamilyList: Story = {
  args: {
    isTutor: false,
    hasFamilyRole: true,
    students: () => Promise.resolve(familyStudents(LUCIA)),
    schedule: () => Promise.resolve(familyPage()),
  },
  play: async ({ canvasElement }) => {
    const region = await events(canvasElement);
    for (const state of [
      'confirmed',
      'completed',
      'cancelled',
      'skipped_closure',
    ] as const) {
      await expect(
        region.getAllByRole('button', {
          name: new RegExp(escape(i18n.t(`mySchedule.states.${state}`))),
        }).length,
      ).toBeGreaterThan(0);
    }
  },
};

export const ChildSelectorWithConsentRequired: Story = {
  args: {
    isTutor: false,
    hasFamilyRole: true,
    initial: { ...LIST, whose: MATEO.student_id },
    students: () => Promise.resolve(familyStudents(LUCIA, MATEO)),
    schedule: () => Promise.resolve(familyPage()),
  },
  play: async ({ canvasElement }) => {
    const canvas = within(canvasElement);
    await expect(
      await canvas.findByRole('combobox', {
        name: i18n.t('mySchedule.whose.label'),
      }),
    ).toHaveValue(MATEO.student_id);
    await expect(
      canvas.getByRole('heading', { name: i18n.t('mySchedule.consent.title') }),
    ).toBeVisible();
    await expect(
      canvas.queryByRole('region', { name: i18n.t('mySchedule.regionLabel') }),
    ).toBeNull();
  },
};

export const Empty: Story = {
  args: { schedule: () => Promise.resolve({ entries: [], truncated: false }) },
  play: async ({ canvasElement }) => {
    await events(canvasElement);
    const statuses = within(canvasElement).getAllByRole('status');
    await expect(
      statuses.some(
        (status) => status.textContent === i18n.t('mySchedule.empty'),
      ),
    ).toBe(true);
  },
};

export const Loading: Story = {
  args: { schedule: () => new Promise<SchedulePage>(() => {}) },
  play: async ({ canvasElement }) => {
    await expect(
      await within(canvasElement).findByRole('region', {
        name: i18n.t('mySchedule.regionLabel'),
      }),
    ).toHaveAttribute('aria-busy', 'true');
  },
};

export const Failed: Story = {
  args: {
    schedule: () =>
      Promise.reject(
        new ApiError({
          code: 'internal_error',
          status: 500,
          requestId: 'req-story-500',
        }),
      ),
  },
  play: async ({ canvasElement }) => {
    await expect(
      await within(canvasElement).findByRole('button', {
        name: i18n.t('feedback.retry'),
      }),
    ).toBeVisible();
  },
};

export const Truncated: Story = {
  args: { schedule: () => Promise.resolve(tutorPage(true)) },
  play: async ({ canvasElement }) => {
    await events(canvasElement);
    await expect(
      within(canvasElement).getByText(i18n.t('mySchedule.truncated')),
    ).toBeVisible();
  },
};

export const DetailsDialog: Story = {
  play: async ({ canvasElement }) => {
    const region = await events(canvasElement);
    const event = region.getByRole('button', {
      name: new RegExp(escape(i18n.t('mySchedule.states.offered'))),
    });
    event.focus();
    await userEvent.keyboard('{Enter}');
    const dialog = await within(document.body).findByRole('dialog');
    await expect(
      within(dialog).getByRole('link', {
        name: i18n.t('mySchedule.answerOffer'),
      }),
    ).toHaveAttribute('href', '/offers');
    await userEvent.keyboard('{Escape}');
    await waitFor(() => expect(event).toHaveFocus());
  },
};

export const Narrow: Story = {
  globals: { viewport: { value: 'narrow320' } },
  args: {
    isTutor: true,
    hasFamilyRole: true,
    students: () => Promise.resolve(familyStudents(LUCIA, MATEO)),
  },
  play: async ({ canvasElement }) => {
    await events(canvasElement);
    const main = canvasElement.querySelector('main')!;
    await expect(main.scrollWidth).toBeLessThanOrEqual(main.clientWidth);
    const toolbar = within(main).getByRole('toolbar');
    for (const button of within(toolbar).getAllByRole('button')) {
      const box = button.getBoundingClientRect();
      if (box.width > 0) {
        await expect(box.width).toBeGreaterThanOrEqual(24);
        await expect(box.height).toBeGreaterThanOrEqual(24);
      }
    }
  },
};
