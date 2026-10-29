import type { Meta, StoryObj } from '@storybook/react-vite';
import { expect } from 'storybook/test';
import { i18n } from '@/i18n';
import { StoryAuth } from '@/test/StoryAuth';
import { StoryRouter } from '@/test/StoryRouter';
import { FINANCE_SESSION } from '../storySession';
import {
  PACKAGE_ACTIVE_DETAIL,
  PACKAGE_EXPIRED_DETAIL,
  PURCHASE_CHARGE,
} from './fixtures';
import { PackagePageView } from './PackagePage';

// The package page without its query (#377 G2): an active package (the
// grant and two sessions used, with links; the purchase charge), an
// expired one (a reversal and the expiry; the charge couldn't be read),
// and one just sold (announced). No request. axe runs on each story in
// both themes.

const meta = {
  title: 'Billing/Packages/PackagePage',
  component: PackagePageView,
  args: {
    pkg: PACKAGE_ACTIVE_DETAIL,
    charge: { status: 'ready', charge: PURCHASE_CHARGE },
    canOpenSessions: true,
  },
  render: (args) => (
    <StoryAuth session={FINANCE_SESSION}>
      <StoryRouter
        path="/billing/packages/x"
        routes={[{ path: '*', element: <PackagePageView {...args} /> }]}
      />
    </StoryAuth>
  ),
} satisfies Meta<typeof PackagePageView>;

export default meta;
type Story = StoryObj<typeof meta>;

export const Active: Story = {
  play: async ({ canvas }) => {
    await expect(
      canvas.getByRole('table', {
        name: i18n.t('billing.packages.history.title'),
      }),
    ).toBeVisible();
    await expect(
      canvas.getAllByRole('link', {
        name: new RegExp(
          i18n.t('billing.packages.history.kinds.consume', { date: '' }),
        ),
      }),
    ).toHaveLength(2);
    await expect(
      canvas.getByText(i18n.t('billing.packages.rules.expiry')),
    ).toBeVisible();
  },
};

export const Expired: Story = {
  args: {
    pkg: PACKAGE_EXPIRED_DETAIL,
    charge: { status: 'unavailable' },
  },
  play: async ({ canvas }) => {
    await expect(
      canvas.getByText(i18n.t('billing.packages.history.kinds.expire'), {
        selector: 'td',
      }),
    ).toBeVisible();
    await expect(
      canvas.getByText(i18n.t('billing.packages.history.kinds.reversal')),
    ).toBeVisible();
  },
};

export const JustSold: Story = {
  args: { announcement: i18n.t('billing.packages.sell.done') },
};
