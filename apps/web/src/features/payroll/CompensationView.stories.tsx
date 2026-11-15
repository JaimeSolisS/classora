import type { Meta, StoryObj } from '@storybook/react-vite';
import { expect, fn } from 'storybook/test';
import { i18n } from '@/i18n';
import { StoryAuth } from '@/test/StoryAuth';
import { CompensationView } from './CompensationView';
import { HOURLY, NOT_SET_UP, SALARIED, SALARY_PLUS, STOPPED } from './fixtures';
import { FINANCE_SESSION } from './storySession';

// A tutor's pay (#425 H2): hourly, salary, salary plus extras, stopped and
// empty, with the staff actions, and My pay with none. No request. axe runs
// on each story in both themes.

const actions = {
  onAddTerms: fn(),
  onAddRate: fn(),
  onDeleteTerms: fn(),
  onDeleteRate: fn(),
};

const meta = {
  title: 'Payroll/CompensationView',
  component: CompensationView,
  args: { compensation: HOURLY, actions },
  render: (args) => (
    <StoryAuth session={FINANCE_SESSION}>
      <main className="p-4">
        <CompensationView {...args} />
      </main>
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
} satisfies Meta<typeof CompensationView>;

export default meta;
type Story = StoryObj<typeof meta>;

export const Hourly: Story = {
  play: async ({ canvas, args, userEvent }) => {
    for (const status of ['current', 'upcoming', 'past'] as const) {
      await expect(
        canvas.getAllByText(i18n.t(`payroll.status.${status}`))[0],
      ).toBeVisible();
    }
    await userEvent.click(
      canvas.getByRole('button', {
        name: i18n.t('payroll.rateDialog.title'),
      }),
    );
    await expect(args.actions?.onAddRate).toHaveBeenCalled();
  },
};

export const Salary: Story = {
  args: { compensation: SALARIED },
  play: async ({ canvas }) => {
    await expect(
      canvas.getByText(i18n.t('payroll.compensation.summary.salary')),
    ).toBeVisible();
    await expect(
      canvas.getAllByText(i18n.t('payroll.compensation.coveredBySalary')),
    ).toHaveLength(3);
  },
};

export const SalaryPlusExtras: Story = {
  args: { compensation: SALARY_PLUS },
  play: async ({ canvas }) => {
    await expect(
      canvas.getByText(i18n.t('payroll.compensation.summary.salaryPlus')),
    ).toBeVisible();
  },
};

export const Stopped: Story = {
  args: { compensation: STOPPED },
};

export const Empty: Story = {
  args: { compensation: NOT_SET_UP },
  play: async ({ canvas }) => {
    await expect(
      canvas.getByText(i18n.t('payroll.compensation.noTerms')),
    ).toBeVisible();
  },
};

export const ReadOnly: Story = {
  args: {
    actions: undefined,
    note: i18n.t('payroll.compensation.ownPay'),
  },
  play: async ({ canvas }) => {
    await expect(canvas.queryByRole('button')).toBeNull();
  },
};

export const MyPay: Story = {
  args: {
    actions: undefined,
    compensation: SALARY_PLUS,
    title: i18n.t('payroll.myPay.title'),
    emptyMessage: i18n.t('payroll.myPay.empty'),
  },
  play: async ({ canvas }) => {
    await expect(canvas.queryByRole('button')).toBeNull();
    await expect(canvas.getByText(/\$250\.125/)).toBeVisible();
  },
};

export const MyPayNotSetUp: Story = {
  args: {
    actions: undefined,
    compensation: NOT_SET_UP,
    title: i18n.t('payroll.myPay.title'),
    emptyMessage: i18n.t('payroll.myPay.empty'),
  },
  play: async ({ canvas }) => {
    await expect(canvas.getByText(i18n.t('payroll.myPay.empty'))).toBeVisible();
  },
};

export const Narrow: Story = {
  globals: { viewport: { value: 'narrow320' } },
  args: { compensation: SALARY_PLUS, actions },
  play: async ({ canvasElement }) => {
    const main = canvasElement.querySelector('main')!;
    await expect(main.scrollWidth).toBeLessThanOrEqual(main.clientWidth);
  },
};
