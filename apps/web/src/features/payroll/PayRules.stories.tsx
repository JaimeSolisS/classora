import type { Meta, StoryObj } from '@storybook/react-vite';
import { expect, fn, within } from 'storybook/test';
import { ApiError } from '@/app/api';
import { i18n } from '@/i18n';
import { parseLocalDate } from '@/lib/time';
import { StoryAuth } from '@/test/StoryAuth';
import { StoryRouter } from '@/test/StoryRouter';
import { MAKEUP_SEED, RULES_SEEDED, RULES_VERSIONED, TODAY } from './fixtures';
import { PayRuleDialog } from './PayRuleDialog';
import { PayRulesView } from './PayRulesView';
import { FINANCE_SESSION } from './storySession';

// The Pay rules tab and its dialog (#444 D2): the seeds, versions with a
// history, read-only, loading, error, a refused delete's text, 320 px, and the
// dialog with a field error. No request. axe runs on each story in both themes.

const meta = {
  title: 'Payroll/PayRulesView',
  component: PayRulesView,
  args: {
    state: { status: 'ready', overview: RULES_SEEDED },
    canManage: true,
    onNewVersion: fn(),
    onDelete: fn(() => Promise.resolve()),
  },
  render: (args) => (
    <StoryAuth session={FINANCE_SESSION}>
      <StoryRouter
        path="/payroll/pay-rules"
        routes={[
          {
            path: '*',
            element: (
              <main>
                <PayRulesView {...args} />
              </main>
            ),
          },
        ]}
      />
    </StoryAuth>
  ),
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
} satisfies Meta<typeof PayRulesView>;

export default meta;
type Story = StoryObj<typeof meta>;

export const Seeds: Story = {
  play: async ({ canvas, args, userEvent }) => {
    await expect(
      canvas.getByRole('link', { name: i18n.t('payroll.tabs.payRules') }),
    ).toHaveAttribute('aria-current', 'page');
    await expect(
      canvas.getByText(i18n.t('payroll.payRules.lateNote')),
    ).toBeVisible();
    await userEvent.click(
      canvas.getByRole('button', {
        name: i18n.t('payroll.payRules.newVersionFor', {
          event: i18n.t('payroll.payRules.events.makeup'),
        }),
      }),
    );
    await expect(args.onNewVersion).toHaveBeenCalledWith('makeup');
  },
};

export const Versions: Story = {
  args: {
    state: { status: 'ready', overview: RULES_VERSIONED },
    initialHistoryOpen: true,
    announcement: i18n.t('payroll.payRules.saved'),
  },
};

export const ReadOnly: Story = {
  args: {
    state: { status: 'ready', overview: RULES_VERSIONED },
    canManage: false,
  },
  play: async ({ canvas }) => {
    await expect(
      canvas.queryByRole('button', { name: /eliminar|delete/i }),
    ).toBeNull();
  },
};

export const Loading: Story = { args: { state: { status: 'loading' } } };

export const LoadError: Story = {
  args: {
    state: { status: 'error', error: new Error('x'), onRetry: fn() },
  },
};

export const Narrow: Story = {
  globals: { viewport: { value: 'narrow320' } },
  args: {
    state: { status: 'ready', overview: RULES_VERSIONED },
    initialHistoryOpen: true,
  },
  play: async ({ canvasElement }) => {
    const main = canvasElement.querySelector('main')!;
    await expect(main.scrollWidth).toBeLessThanOrEqual(main.clientWidth);
    const targets = main.querySelectorAll<HTMLElement>('button, a');
    await expect(targets.length).toBeGreaterThan(0);
    for (const target of targets) {
      await expect(
        target.getBoundingClientRect().height,
      ).toBeGreaterThanOrEqual(44);
    }
  },
};

const dialogProps = {
  open: true,
  today: parseLocalDate(TODAY),
  latest: MAKEUP_SEED,
  onSubmit: fn(() => Promise.resolve()),
  onReload: fn(() => Promise.resolve()),
  onClose: fn(),
};

export const Dialog: Story = {
  render: () => (
    <StoryRouter
      routes={[{ path: '*', element: <PayRuleDialog {...dialogProps} /> }]}
    />
  ),
  play: async ({ canvasElement }) => {
    const body = within(canvasElement.ownerDocument.body);
    await expect(
      body.getByLabelText(i18n.t('payroll.payRules.fields.effectiveFrom')),
    ).toHaveAttribute('min', TODAY);
    await expect(
      body.getByLabelText(i18n.t('payroll.payRules.fields.percent')),
    ).toHaveValue('100');
  },
};

export const DialogNarrow: Story = {
  globals: { viewport: { value: 'narrow320' } },
  render: () => (
    <StoryRouter
      routes={[{ path: '*', element: <PayRuleDialog {...dialogProps} /> }]}
    />
  ),
  play: async ({ canvasElement }) => {
    const sheet = within(canvasElement.ownerDocument.body).getByRole('dialog');
    await expect(sheet.scrollWidth).toBeLessThanOrEqual(sheet.clientWidth);
    for (const target of sheet.querySelectorAll<HTMLElement>(
      'button, input, select',
    )) {
      await expect(
        target.getBoundingClientRect().height,
      ).toBeGreaterThanOrEqual(44);
    }
  },
};

export const DialogFieldError: Story = {
  render: () => (
    <StoryRouter
      routes={[
        {
          path: '*',
          element: (
            <PayRuleDialog
              {...dialogProps}
              initialValues={{
                mode: 'percent',
                percent: '1500',
                amount: '',
                effectiveFrom: '2026-11-01',
              }}
              initialErrors={{
                percent: 'payroll.payRules.validation.percentRange',
              }}
            />
          ),
        },
      ]}
    />
  ),
  play: async ({ canvasElement }) => {
    await expect(
      within(canvasElement.ownerDocument.body).getByText(
        i18n.t('payroll.payRules.validation.percentRange'),
      ),
    ).toBeVisible();
  },
};

export const DialogLateCancellation: Story = {
  render: () => (
    <StoryRouter
      routes={[
        {
          path: '*',
          element: (
            <PayRuleDialog
              {...dialogProps}
              latest={RULES_SEEDED.late_cancellation.current}
            />
          ),
        },
      ]}
    />
  ),
  play: async ({ canvasElement }) => {
    await expect(
      within(canvasElement.ownerDocument.body).getByText(
        i18n.t('payroll.payRules.lateNote'),
      ),
    ).toBeVisible();
  },
};

/** A 409 version_conflict that a failed reload left in place. */
export const DialogConflict: Story = {
  render: () => {
    const refused = (
      <PayRuleDialog
        {...dialogProps}
        onSubmit={() =>
          Promise.reject(
            new ApiError({ status: 409, code: 'version_conflict' }),
          )
        }
        initialValues={{
          mode: 'none',
          percent: '',
          amount: '',
          effectiveFrom: '2026-11-01',
        }}
      />
    );
    return <StoryRouter routes={[{ path: '*', element: refused }]} />;
  },
  play: async ({ canvasElement, userEvent }) => {
    const body = within(canvasElement.ownerDocument.body);
    await userEvent.click(
      body.getByRole('button', { name: i18n.t('payroll.payRules.save') }),
    );
    await expect(
      await body.findByRole('button', {
        name: i18n.t('payroll.payRules.reload'),
      }),
    ).toBeVisible();
  },
};
