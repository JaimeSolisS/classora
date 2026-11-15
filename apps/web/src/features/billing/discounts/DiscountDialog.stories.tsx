import type { Meta, StoryObj } from '@storybook/react-vite';
import { expect, fn, within } from 'storybook/test';
import { i18n } from '@/i18n';
import { StoryRouter } from '@/test/StoryRouter';
import { LEVEL_OPTIONS, SUBJECT_OPTIONS } from '../pricing/fixtures';
import { DiscountDialog } from './DiscountDialog';
import { SCHOLARSHIP_DETAIL } from './fixtures';
import { emptyDiscountForm } from './rules';

// "Add discount" and "Edit discount" (#367 G2): the empty create form, the
// edit of a scholarship (kind and who gets it read-only, the C4 note) and
// a sibling discount (everyone forced, the minimum shown) with the client
// checks. No request: every call is a stub. The dialog is portalled to
// <body>. axe runs on each story in both themes.

const meta = {
  title: 'Billing/DiscountDialog',
  component: DiscountDialog,
  args: {
    open: true,
    subjects: SUBJECT_OPTIONS,
    levels: LEVEL_OPTIONS,
    onSubmit: fn(() => Promise.resolve()),
    onClose: fn(),
  },
  render: (args) => (
    <StoryRouter
      path="/billing/discounts"
      routes={[{ path: '*', element: <DiscountDialog {...args} /> }]}
    />
  ),
} satisfies Meta<typeof DiscountDialog>;

export default meta;
type Story = StoryObj<typeof meta>;

type Context = Parameters<NonNullable<Story['play']>>[0];

const body = (context: Context) =>
  within(context.canvasElement.ownerDocument.body);

export const Create: Story = {
  play: async (context) => {
    const dialog = await body(context).findByRole('dialog', {
      name: i18n.t('billing.discounts.add'),
    });
    await expect(
      dialog.querySelector('input[type="radio"][value="discount"]'),
    ).toBeChecked();
    await expect(
      within(dialog).getByLabelText(i18n.t('billing.discounts.fields.percent')),
    ).toHaveAccessibleDescription(i18n.t('billing.discounts.hints.percent'));
  },
};

export const Edit: Story = {
  args: { discount: SCHOLARSHIP_DETAIL },
  play: async (context) => {
    const dialog = await body(context).findByRole('dialog', {
      name: i18n.t('billing.discounts.editTitle'),
    });
    await expect(
      within(dialog).getByText(i18n.t('billing.discounts.editNote')),
    ).toBeVisible();
    await expect(
      within(dialog).getByLabelText(i18n.t('billing.discounts.fields.percent')),
    ).toHaveValue('50');
    await expect(
      within(dialog).getByRole('checkbox', { name: 'Álgebra' }),
    ).toBeChecked();
  },
};

export const Sibling: Story = {
  args: {
    initialValues: {
      ...emptyDiscountForm(),
      kind: 'sibling',
      name: 'Hermanos',
      value: '10',
    },
  },
  play: async (context) => {
    const dialog = await body(context).findByRole('dialog');
    await expect(
      within(dialog).getByRole('radio', {
        name: i18n.t('billing.discounts.audience.assigned'),
      }),
    ).toBeDisabled();
    await context.userEvent.click(
      within(dialog).getByRole('button', {
        name: i18n.t('billing.discounts.save'),
      }),
    );
    await expect(
      within(dialog).getByLabelText(
        i18n.t('billing.discounts.fields.minFamilyStudents'),
      ),
    ).toHaveAttribute('aria-invalid', 'true');
    await expect(context.args.onSubmit).not.toHaveBeenCalled();
  },
};
