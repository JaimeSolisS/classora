import type { Meta, StoryObj } from '@storybook/react-vite';
import { expect, fn, within } from 'storybook/test';
import { ApiError } from '@/app/api';
import { i18n } from '@/i18n';
import { StoryRouter } from '@/test/StoryRouter';
import { DueOffsetDialog } from './DueOffsetDialog';

// "Extra days to pay" (#382 F2): the current offset with the hint that it
// applies to new charges only, and the version-conflict message. No
// request: every call is a stub. The dialog is portalled to <body>. axe
// runs on each story in both themes.

const meta = {
  title: 'Billing/DueDates/DueOffsetDialog',
  component: DueOffsetDialog,
  args: {
    open: true,
    current: 7,
    onSubmit: fn(() => Promise.resolve()),
    onConflict: fn(),
    onClose: fn(),
  },
  render: (args) => (
    <StoryRouter
      path="/billing/accounts/x"
      routes={[{ path: '*', element: <DueOffsetDialog {...args} /> }]}
    />
  ),
} satisfies Meta<typeof DueOffsetDialog>;

export default meta;
type Story = StoryObj<typeof meta>;

type Context = Parameters<NonNullable<Story['play']>>[0];

const dialog = (context: Context) =>
  within(context.canvasElement.ownerDocument.body).getByRole('dialog', {
    name: i18n.t('billing.dueDates.offset.title'),
  });

export const Default: Story = {
  play: async (context) => {
    await expect(
      within(dialog(context)).getByLabelText(
        i18n.t('billing.dueDates.offset.field'),
      ),
    ).toHaveValue(7);
    await expect(
      within(dialog(context)).getByText(i18n.t('billing.dueDates.offset.hint')),
    ).toBeVisible();
  },
};

export const Conflict: Story = {
  args: {
    initialFailure: new ApiError({
      code: 'version_conflict',
      status: 409,
      requestId: 'req-due-offset',
    }),
  },
  play: async (context) => {
    await expect(within(dialog(context)).getByRole('alert')).toHaveTextContent(
      i18n.t('billing.dueDates.offset.conflict'),
    );
  },
};
