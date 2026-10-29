import type { Meta, StoryObj } from '@storybook/react-vite';
import { expect, fn } from 'storybook/test';
import { i18n } from '@/i18n';
import { StoryAuth } from '@/test/StoryAuth';
import { StoryRouter } from '@/test/StoryRouter';
import { FINANCE_SESSION } from '../storySession';
import { AccountsView } from './AccountsView';
import { EMPTY_FAMILIES_PAGE, FAMILIES_PAGE } from './fixtures';

// The family accounts list without its query (#379 G2): rows (an active
// and an inactive family) and the empty search. No request. axe runs on
// each story in both themes.

const meta = {
  title: 'Billing/Accounts/AccountsView',
  component: AccountsView,
  args: {
    state: { status: 'ready', page: FAMILIES_PAGE },
    text: '',
    onText: fn(),
    status: 'active',
    onStatus: fn(),
    searching: false,
    onPage: fn(),
  },
  render: (args) => (
    <StoryAuth session={FINANCE_SESSION}>
      <StoryRouter
        path="/billing/accounts"
        routes={[{ path: '*', element: <AccountsView {...args} /> }]}
      />
    </StoryAuth>
  ),
} satisfies Meta<typeof AccountsView>;

export default meta;
type Story = StoryObj<typeof meta>;

export const Rows: Story = {
  play: async ({ canvas }) => {
    await expect(
      canvas.getByRole('link', { name: i18n.t('billing.tabs.accounts') }),
    ).toHaveAttribute('aria-current', 'page');
    await expect(
      canvas.getByRole('table', {
        name: i18n.t('billing.accounts.list.title'),
      }),
    ).toBeVisible();
    await expect(
      canvas.getByRole('link', { name: 'Familia López' }),
    ).toHaveAttribute(
      'href',
      `/billing/accounts/${FAMILIES_PAGE.items[0]!.id}`,
    );
  },
};

export const Empty: Story = {
  args: {
    state: { status: 'ready', page: EMPTY_FAMILIES_PAGE },
    text: 'zzz',
    searching: true,
  },
  play: async ({ canvas }) => {
    await expect(
      canvas.getByText(i18n.t('billing.accounts.list.noResults')),
    ).toBeVisible();
  },
};
