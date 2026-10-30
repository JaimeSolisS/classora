import type { Meta, StoryObj } from '@storybook/react-vite';
import { expect, fn, within } from 'storybook/test';
import { i18n } from '@/i18n';
import type { LocalDate } from '@/lib/time';
import { StoryRouter } from '@/test/StoryRouter';
import { PAYMENT_METHODS, TODAY } from '../accounts/fixtures';
import {
  blockedPreview,
  COHORT_PREVIEW,
  GROUP_PREVIEW,
  PACKAGE_PREVIEW,
  PACKAGE_PREVIEW_FRESH,
  PACKAGE_PREVIEW_HOURS,
  PACKAGE_PREVIEW_PAID,
} from './fixtures';
import { WithdrawalDialog } from './WithdrawalDialog';
import { WithdrawalSettlement } from './WithdrawalSettlement';

// The withdrawal dialog and its settlement panel (#387 D2): a cohort
// enrollment, a group enrollment with a fee, a tuition period posted now
// and automatic session credits, a package of hours, a paid package (Refund
// offered), the lines adjusted (an estimate), a blocking reason, the stale
// state, the read-only panel, and the roster's first step. No request: the
// confirms are stubs. The dialog is portalled to <body>. Axe runs on each
// story in both themes.

const meta = {
  title: 'Billing/Withdrawals/WithdrawalDialog',
  component: WithdrawalDialog,
  args: {
    open: true,
    subjectKey: 'story',
    title: i18n.t('billing.withdrawals.package.title'),
    subjectName: 'Álgebra 10 sesiones',
    state: { status: 'ready', preview: PACKAGE_PREVIEW },
    canCredit: true,
    canRefund: true,
    methods: PAYMENT_METHODS,
    today: TODAY as LocalDate,
    onConfirm: fn(() => Promise.resolve()),
    onStale: fn(),
    onRefetch: fn(),
    onClose: fn(),
  },
  render: (args) => (
    <StoryRouter
      path="/billing/packages"
      routes={[{ path: '*', element: <WithdrawalDialog {...args} /> }]}
    />
  ),
} satisfies Meta<typeof WithdrawalDialog>;

export default meta;
type Story = StoryObj<typeof meta>;
type Context = Parameters<NonNullable<Story['play']>>[0];

const findDialog = async (context: Context) =>
  within(
    await within(context.canvasElement.ownerDocument.body).findByRole('dialog'),
  );

export const Package: Story = {
  play: async (context) => {
    const dialog = await findDialog(context);
    await expect(
      dialog.getByText(i18n.t('billing.withdrawals.policies.pro_rata')),
    ).toBeVisible();
    await expect(
      dialog.getByRole('radio', {
        name: i18n.t('billing.withdrawals.outcomes.refund'),
      }),
    ).toBeDisabled();
  },
};

export const PackageHours: Story = {
  args: { state: { status: 'ready', preview: PACKAGE_PREVIEW_HOURS } },
};

export const PackagePaidRefund: Story = {
  args: { state: { status: 'ready', preview: PACKAGE_PREVIEW_PAID } },
  play: async (context) => {
    const dialog = await findDialog(context);
    await context.userEvent.click(
      dialog.getByRole('radio', {
        name: i18n.t('billing.withdrawals.outcomes.refund'),
      }),
    );
    await expect(
      dialog.getByLabelText(i18n.t('billing.withdrawals.fields.method')),
    ).toBeVisible();
  },
};

export const GroupWithAutomaticCredits: Story = {
  args: {
    title: i18n.t('groups.withdraw.title'),
    subjectName: 'Álgebra sábado',
    state: { status: 'ready', preview: GROUP_PREVIEW },
  },
  play: async (context) => {
    const dialog = await findDialog(context);
    await expect(
      dialog.getByText(i18n.t('billing.withdrawals.lines.postedNow')),
    ).toBeVisible();
  },
};

export const Cohort: Story = {
  args: {
    title: i18n.t('courses.cohorts.enrollment.withdrawDialog.title'),
    subjectName: 'Álgebra enero',
    state: { status: 'ready', preview: COHORT_PREVIEW },
  },
};

export const Blocked: Story = {
  args: {
    state: {
      status: 'ready',
      preview: blockedPreview('withdrawal_closeout_pending'),
    },
  },
  play: async (context) => {
    const dialog = await findDialog(context);
    await expect(
      dialog.queryByRole('button', {
        name: i18n.t('billing.withdrawals.confirm'),
      }),
    ).toBeNull();
  },
};

export const AlreadySettled: Story = {
  args: {
    state: { status: 'ready', preview: blockedPreview('withdrawal_exists') },
  },
};

export const Loading: Story = {
  args: { state: { status: 'loading' } },
};

export const RosterFirstStep: Story = {
  args: {
    title: i18n.t('groups.withdraw.title'),
    subjectName: 'Álgebra sábado',
    state: { status: 'ready', preview: GROUP_PREVIEW },
    firstStep: {
      description: i18n.t('groups.withdraw.description', {
        name: 'Ana López',
        group: 'Álgebra sábado',
      }),
      reason: i18n.t('billing.withdrawals.fields.reason'),
      keep: i18n.t('groups.withdraw.keep'),
      next: i18n.t('billing.withdrawals.dialog.next'),
    },
  },
  play: async (context) => {
    const dialog = await findDialog(context);
    await context.userEvent.click(
      dialog.getByRole('button', {
        name: i18n.t('billing.withdrawals.dialog.next'),
      }),
    );
    await expect(
      dialog.getByText(i18n.t('billing.withdrawals.dialog.stepTwo')),
    ).toBeVisible();
  },
};

// The panel alone, in a page (its stale and read-only states).

type PanelStory = StoryObj<typeof WithdrawalSettlement>;

function panel(extra: Partial<Parameters<typeof WithdrawalSettlement>[0]>) {
  return (
    <StoryRouter
      path="/billing/packages"
      routes={[
        {
          path: '*',
          element: (
            <main className="p-4">
              <WithdrawalSettlement
                preview={PACKAGE_PREVIEW_FRESH}
                subjectName="Álgebra 10 sesiones"
                canCredit
                canRefund
                methods={PAYMENT_METHODS}
                today={TODAY as LocalDate}
                onConfirm={fn(() => Promise.resolve())}
                onStale={fn()}
                onRefetch={fn()}
                onCancel={fn()}
                busyRef={{ current: false }}
                {...extra}
              />
            </main>
          ),
        },
      ]}
    />
  );
}

export const PanelStale: PanelStory = {
  render: () =>
    panel({
      initialStale: true,
      initialChanged: ['credit', 'reduces_debt'],
      initialValues: { reason: 'La familia se muda' },
    }),
  play: async ({ canvasElement }) => {
    await expect(
      within(canvasElement).getByText(i18n.t('billing.withdrawals.stale')),
    ).toBeVisible();
  },
};

export const PanelAdjusted: PanelStory = {
  render: () =>
    panel({
      preview: GROUP_PREVIEW,
      initialAdjusting: true,
      initialValues: {
        overrides: { 'e1000000-0000-4000-8000-000000000001': '500' },
      },
    }),
  play: async ({ canvasElement }) => {
    await expect(
      within(canvasElement).getByText(
        i18n.t('billing.withdrawals.totals.estimate'),
      ),
    ).toBeVisible();
  },
};

export const PanelReadOnly: PanelStory = {
  render: () => panel({ canCredit: false, canRefund: false }),
  play: async ({ canvasElement }) => {
    await expect(
      within(canvasElement).getByText(
        i18n.t('billing.withdrawals.outcome.readOnly'),
      ),
    ).toBeVisible();
  },
};
