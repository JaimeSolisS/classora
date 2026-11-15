import type { Meta, StoryObj } from '@storybook/react-vite';
import { expect, fn, userEvent, within } from 'storybook/test';
import { i18n } from '@/i18n';
import type { LocalDate } from '@/lib/time';
import { StoryRouter } from '@/test/StoryRouter';
import { TODAY } from './fixtures';
import { RateDialog } from './RateDialog';

// "Add rate" (#425 H2): per_course left out for a group, and the no-rate
// toggle. No request: every call is a stub. axe runs on each story in both
// themes.

const meta = {
  title: 'Payroll/RateDialog',
  component: RateDialog,
  args: {
    open: true,
    today: TODAY as LocalDate,
    onSubmit: fn(() => Promise.resolve()),
    onClose: fn(),
  },
  render: (args) => (
    <StoryRouter routes={[{ path: '*', element: <RateDialog {...args} /> }]} />
  ),
} satisfies Meta<typeof RateDialog>;

const narrowViewport = {
  viewport: {
    options: {
      narrow320: {
        name: 'Narrow (320 px)',
        styles: { width: '320px', height: '640px' },
        type: 'mobile',
      },
    },
  },
};

export default meta;
type Story = StoryObj<typeof meta>;

const body = (element: HTMLElement) => within(element.ownerDocument.body);

export const Rate: Story = {
  play: async ({ canvasElement }) => {
    const canvas = body(canvasElement);
    await userEvent.selectOptions(
      canvas.getByLabelText(i18n.t('payroll.fields.classKind')),
      'group',
    );
    await expect(
      canvas.queryByRole('option', {
        name: i18n.t('payroll.models.per_course'),
      }),
    ).toBeNull();
  },
};

export const RateNoRate: Story = {
  play: async ({ canvasElement }) => {
    const canvas = body(canvasElement);
    await userEvent.click(
      canvas.getByRole('checkbox', {
        name: i18n.t('payroll.rateDialog.noRate'),
      }),
    );
    await expect(
      canvas.getByLabelText(i18n.t('payroll.fields.rate')),
    ).toBeDisabled();
  },
};

/** G1: below 768 px the checkbox and its label are 44 px high. */
export const RateTargets: Story = {
  parameters: narrowViewport,
  globals: { viewport: { value: 'narrow320' } },
  play: async ({ canvasElement }) => {
    const canvas = body(canvasElement);
    const box = canvas.getByRole('checkbox', {
      name: i18n.t('payroll.rateDialog.noRate'),
    });
    const after = getComputedStyle(box, '::after');
    await expect(parseFloat(after.height)).toBeGreaterThanOrEqual(44);
    await expect(parseFloat(after.width)).toBeGreaterThanOrEqual(44);
    const label = canvas.getByText(i18n.t('payroll.rateDialog.noRate'));
    await expect(label.getBoundingClientRect().height).toBeGreaterThanOrEqual(
      44,
    );
  },
};
