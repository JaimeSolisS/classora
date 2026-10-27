import type { Meta, StoryObj } from '@storybook/react-vite';
import { expect, userEvent, within } from 'storybook/test';
import { ApiError } from '@/app/api';
import { i18n } from '@/i18n';
import type { CohortCompletion } from './completion/api';
import {
  CompletionSection,
  type CompletionState,
} from './completion/CompletionSection';
import {
  ANA_COMPLETION,
  COMPLETION,
  EMPTY_COMPLETION,
  NO_CRITERIA_COMPLETION,
  OWN_COMPLETION,
  TWO_CHILDREN_COMPLETION,
} from './completion/fixtures';
import { MyProgressSection } from './completion/MyProgressSection';

// Course completion on the cohort page (#323 W7): the roster (mixed
// statuses, one awaiting closeout, Ana's detail open with a module covered
// by the course), an empty roster, no criteria, the family's own section
// and a guardian's two, loading and error. At 320 and 390 px the table
// scrolls inside its focusable region and the page never scrolls sideways;
// at 1280 px the table fits. Axe runs in light and dark (the storybook
// projects). No request leaves the story.

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

type Viewport = keyof typeof VIEWPORTS;

interface StoryArgs {
  roster?: CompletionState;
  family?: { data: CohortCompletion; heading: 'mine' | 'named' };
  initiallyOpen?: readonly string[];
}

function CompletionStory({ roster, family, initiallyOpen }: StoryArgs) {
  return (
    <main className="flex w-full max-w-4xl min-w-0 flex-col gap-8 p-4">
      {roster ? (
        <CompletionSection state={roster} initiallyOpen={initiallyOpen} />
      ) : null}
      {family ? (
        <MyProgressSection data={family.data} heading={family.heading} />
      ) : null}
    </main>
  );
}

const meta = {
  title: 'Courses/CohortCompletion',
  component: CompletionStory,
  parameters: { viewport: { options: VIEWPORTS } },
} satisfies Meta<typeof CompletionStory>;

export default meta;
type Story = StoryObj<typeof meta>;

const key = 'courses.cohorts.completion';

async function tableRegion(canvasElement: HTMLElement) {
  const region = within(canvasElement).getByRole('region', {
    name: i18n.t(`${key}.caption`),
  });
  await expect(region).toHaveAttribute('tabindex', '0');
  return region;
}

async function noSideScroll(canvasElement: HTMLElement) {
  const page = canvasElement.ownerDocument.documentElement;
  await expect(page.scrollWidth).toBeLessThanOrEqual(page.clientWidth);
}

const at = (story: Story, value: Viewport): Story => ({
  ...story,
  globals: { viewport: { value } },
  play: async (context) => {
    await story.play?.(context);
    if (value !== 'desktop1280') {
      await noSideScroll(context.canvasElement);
    }
  },
});

export const Roster: Story = {
  args: {
    roster: { status: 'ready', data: COMPLETION },
    initiallyOpen: [ANA_COMPLETION.student.id],
  },
  play: async ({ canvasElement }) => {
    const canvas = within(canvasElement);
    await tableRegion(canvasElement);
    await expect(
      canvas.getByRole('heading', { level: 2, name: i18n.t(`${key}.heading`) }),
    ).toBeVisible();
    const ana = canvas.getByRole('button', {
      name: i18n.t(`${key}.detailsFor`, { name: 'Ana López' }),
    });
    await expect(ana).toHaveAttribute('aria-expanded', 'true');
    await expect(
      canvas.getByText(i18n.t(`${key}.detail.courseCovered`)),
    ).toBeVisible();
    await expect(
      canvas.getAllByText(i18n.t(`${key}.awaiting`, { count: 1 }))[0],
    ).toBeVisible();
    // The disclosure keeps the focus when toggled from the keyboard.
    const bruno = canvas.getByRole('button', {
      name: i18n.t(`${key}.detailsFor`, { name: 'Bruno Díaz' }),
    });
    bruno.focus();
    await userEvent.keyboard('{Enter}');
    await expect(bruno).toHaveAttribute('aria-expanded', 'true');
    await expect(bruno).toHaveFocus();
    await userEvent.keyboard(' ');
    await expect(bruno).toHaveAttribute('aria-expanded', 'false');
  },
};
/** The open detail reflows inside the visible region, never beyond it. */
async function detailFits(canvasElement: HTMLElement) {
  const region = await tableRegion(canvasElement);
  const ana = within(canvasElement).getByRole('button', {
    name: i18n.t(`${key}.detailsFor`, { name: 'Ana López' }),
  });
  const detail = canvasElement.ownerDocument.getElementById(
    ana.getAttribute('aria-controls')!,
  )!;
  const content = detail.querySelector('td > div')!;
  await expect(content.getBoundingClientRect().width).toBeLessThanOrEqual(
    region.clientWidth,
  );
}

const phone = (value: 'narrow320' | 'phone390'): Story => {
  const story = at(Roster, value);
  return {
    ...story,
    play: async (context) => {
      await story.play?.(context);
      const region = await tableRegion(context.canvasElement);
      await expect(region.scrollWidth).toBeGreaterThan(region.clientWidth);
      await detailFits(context.canvasElement);
    },
  };
};
export const RosterNarrow320 = phone('narrow320');
export const RosterPhone390 = phone('phone390');
export const RosterDesktop1280: Story = {
  ...at(Roster, 'desktop1280'),
  play: async (context) => {
    await Roster.play?.(context);
    const region = await tableRegion(context.canvasElement);
    await expect(region.scrollWidth).toBeLessThanOrEqual(region.clientWidth);
  },
};

export const RosterEmpty: Story = {
  args: { roster: { status: 'ready', data: EMPTY_COMPLETION } },
  play: async ({ canvasElement }) => {
    await expect(
      within(canvasElement).getByText(i18n.t(`${key}.empty`)),
    ).toBeVisible();
    await expect(within(canvasElement).queryByRole('table')).toBeNull();
  },
};

export const NoCriteria: Story = {
  args: { roster: { status: 'ready', data: NO_CRITERIA_COMPLETION } },
  play: async ({ canvasElement }) => {
    await expect(
      within(canvasElement).getByText(i18n.t(`${key}.criteria.none`)),
    ).toBeVisible();
  },
};

export const MyProgress: Story = {
  args: { family: { data: OWN_COMPLETION, heading: 'mine' } },
  play: async ({ canvasElement }) => {
    const canvas = within(canvasElement);
    await expect(
      canvas.getByRole('region', { name: i18n.t(`${key}.myHeading`) }),
    ).toBeVisible();
    await expect(canvas.queryByRole('table')).toBeNull();
  },
};
export const MyProgressNarrow320 = at(MyProgress, 'narrow320');
export const MyProgressPhone390 = at(MyProgress, 'phone390');
export const MyProgressDesktop1280 = at(MyProgress, 'desktop1280');

export const MyProgressTwoChildren: Story = {
  args: { family: { data: TWO_CHILDREN_COMPLETION, heading: 'named' } },
  play: async ({ canvasElement }) => {
    const canvas = within(canvasElement);
    for (const name of ['Ana López', 'Bruno Díaz']) {
      await expect(
        canvas.getByRole('heading', {
          level: 2,
          name: i18n.t(`${key}.studentHeading`, { name }),
        }),
      ).toBeVisible();
    }
  },
};

export const Loading: Story = {
  args: { roster: { status: 'loading' } },
  play: async ({ canvasElement }) => {
    await expect(
      canvasElement.querySelector('[aria-busy="true"]'),
    ).not.toBeNull();
  },
};

export const LoadError: Story = {
  args: {
    roster: {
      status: 'error',
      error: new ApiError({ code: 'internal_error', status: 500 }),
      onRetry: () => undefined,
    },
  },
  play: async ({ canvasElement }) => {
    await expect(within(canvasElement).getByRole('alert')).toBeVisible();
  },
};
