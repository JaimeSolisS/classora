import type { Meta, StoryObj } from '@storybook/react-vite';
import { expect, fn, within } from 'storybook/test';
import { i18n } from '@/i18n';
import { StoryAuth } from '@/test/StoryAuth';
import { StoryRouter } from '@/test/StoryRouter';
import { FINANCE_SESSION } from '../storySession';
import {
  PLAN_ACTIVE_DETAIL,
  PLAN_ENDED_DETAIL,
  PLAN_PAUSED_DETAIL,
  PLAN_UNCHARGED_DETAIL,
} from './fixtures';
import { TuitionPlanPageView } from './TuitionPlanPage';

// The plan page without its query (#374 H2): an active plan (a charged
// period, upcoming ones, a future pause), a paused plan (Resume), an ended
// plan withdrawn from its group (no actions; its future periods read
// enrollment ended, QA D1), a plan with nothing charged (Delete), read-only, and
// the Pause dialog. No request: every call is a stub. axe runs on each
// story in both themes.

const meta = {
  title: 'Billing/TuitionPlanPage',
  component: TuitionPlanPageView,
  args: {
    plan: PLAN_ACTIVE_DETAIL,
    canManage: true,
    onPause: fn(() => Promise.resolve()),
    onResume: fn(() => Promise.resolve()),
    onUpdate: fn(() => Promise.resolve()),
    onDelete: fn(() => Promise.resolve()),
    onReload: fn(),
  },
  render: (args) => (
    <StoryAuth session={FINANCE_SESSION}>
      <StoryRouter
        path="/billing/tuition-plans/x"
        routes={[{ path: '*', element: <TuitionPlanPageView {...args} /> }]}
      />
    </StoryAuth>
  ),
} satisfies Meta<typeof TuitionPlanPageView>;

export default meta;
type Story = StoryObj<typeof meta>;

type Context = Parameters<NonNullable<Story['play']>>[0];

const body = (context: Context) =>
  within(context.canvasElement.ownerDocument.body);

export const Active: Story = {
  play: async ({ canvas }) => {
    await expect(
      canvas.getByRole('heading', {
        level: 1,
        name: i18n.t('billing.tuition.detailTitleOf', {
          student: PLAN_ACTIVE_DETAIL.student.name,
        }),
      }),
    ).toBeVisible();
    await expect(
      canvas.getByRole('table', {
        name: i18n.t('billing.tuition.periods.upcoming'),
      }),
    ).toBeVisible();
    await expect(
      canvas.getByRole('button', {
        name: i18n.t('billing.tuition.pause.action'),
      }),
    ).toBeVisible();
  },
};

export const Paused: Story = {
  args: { plan: PLAN_PAUSED_DETAIL },
  play: async ({ canvas }) => {
    await expect(
      canvas.getByRole('button', {
        name: i18n.t('billing.tuition.resume.action'),
      }),
    ).toBeVisible();
    await expect(
      canvas.queryByRole('button', {
        name: i18n.t('billing.tuition.pause.action'),
      }),
    ).toBeNull();
  },
};

export const Ended: Story = {
  args: { plan: PLAN_ENDED_DETAIL },
  play: async ({ canvas }) => {
    await expect(
      canvas.getAllByText(i18n.t('billing.tuition.status.ended'))[0],
    ).toBeVisible();
    await expect(canvas.queryAllByRole('button')).toHaveLength(0);
    // QA D1: the withdrawn plan's 11 future periods read enrollment ended.
    await expect(
      canvas.getAllByText(
        i18n.t('billing.tuition.periodStates.enrollment_ended'),
      ),
    ).toHaveLength(11);
    await expect(
      canvas.queryByText(i18n.t('billing.tuition.periodStates.upcoming')),
    ).toBeNull();
  },
};

export const NothingCharged: Story = {
  args: { plan: PLAN_UNCHARGED_DETAIL },
  play: async ({ canvas }) => {
    await expect(
      canvas.getByRole('button', { name: i18n.t('billing.tuition.delete') }),
    ).toBeVisible();
    await expect(
      canvas.getByText(i18n.t('billing.tuition.reasons.no_price')),
    ).toBeVisible();
  },
};

export const ReadOnly: Story = {
  args: { canManage: false },
  play: async ({ canvas }) => {
    await expect(canvas.queryAllByRole('button')).toHaveLength(0);
  },
};

export const PauseDialog: Story = {
  play: async (context) => {
    await context.userEvent.click(
      context.canvas.getByRole('button', {
        name: i18n.t('billing.tuition.pause.action'),
      }),
    );
    const dialog = await body(context).findByRole('dialog', {
      name: i18n.t('billing.tuition.pause.title'),
    });
    await expect(
      within(dialog).getByLabelText(i18n.t('billing.tuition.pause.from')),
    ).toHaveValue('2026-11-01');
  },
};
