import type { Meta, StoryObj } from '@storybook/react-vite';
import { expect, fn, userEvent, within } from 'storybook/test';
import { ApiError } from '@/app/api';
import { i18n } from '@/i18n';
import { StoryAuth } from '@/test/StoryAuth';
import { StoryRouter } from '@/test/StoryRouter';
import { HOURLY, RATE_CURRENT } from './fixtures';
import { FINANCE_SESSION } from './storySession';
import { TutorPayPageView } from './TutorPayPage';

// The tutor pay page without its query (#425 H2): a delete refused because
// the version has been paid, and the non-Owner's own read-only page. No
// request: every call is a stub. axe runs on each story in both themes.

const meta = {
  title: 'Payroll/TutorPayPageView',
  component: TutorPayPageView,
  args: {
    compensation: HOURLY,
    canEdit: true,
    ownPay: false,
    onAddTerms: fn(() => Promise.resolve()),
    onAddRate: fn(() => Promise.resolve()),
    onDelete: fn(() => Promise.resolve()),
    onReload: fn(() => Promise.resolve()),
  },
  render: (args) => (
    <StoryAuth session={FINANCE_SESSION}>
      <StoryRouter
        path="/payroll/tutors/1"
        routes={[{ path: '*', element: <TutorPayPageView {...args} /> }]}
      />
    </StoryAuth>
  ),
} satisfies Meta<typeof TutorPayPageView>;

export default meta;
type Story = StoryObj<typeof meta>;

export const DeleteRefused: Story = {
  args: {
    onDelete: fn(() =>
      Promise.reject(
        new ApiError({ status: 409, code: 'compensation_rate_in_use' }),
      ),
    ),
  },
  play: async ({ canvasElement }) => {
    const page = within(canvasElement.ownerDocument.body);
    const row = canvasElement.querySelector<HTMLElement>(
      `[data-version-id="${RATE_CURRENT.id}"]`,
    )!;
    await userEvent.click(within(row).getByRole('button'));
    const dialog = page.getByRole('dialog', {
      name: i18n.t('payroll.deleteDialog.title'),
    });
    await userEvent.click(
      within(dialog).getByRole('button', {
        name: i18n.t('payroll.deleteDialog.confirm'),
      }),
    );
    await expect(
      await within(dialog).findByText(
        i18n.t('errors:compensation_rate_in_use'),
      ),
    ).toBeVisible();
  },
};

export const OwnPay: Story = {
  args: { canEdit: false, ownPay: true },
  play: async ({ canvas }) => {
    await expect(
      canvas.getByText(i18n.t('payroll.compensation.ownPay')),
    ).toBeVisible();
    await expect(
      canvas.queryByRole('button', {
        name: i18n.t('payroll.rateDialog.title'),
      }),
    ).toBeNull();
  },
};
