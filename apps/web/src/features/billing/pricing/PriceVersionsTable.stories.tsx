import type { Meta, StoryObj } from '@storybook/react-vite';
import { expect, fn } from 'storybook/test';
import { i18n } from '@/i18n';
import { PRICE_DETAIL, V_NOT_OFFERED } from './fixtures';
import { PriceVersionsTable } from './PriceVersionsTable';

// An item's versions (#363 F3): upcoming (with Delete), current and past
// badges; a "not offered" version; no versions. No request. axe runs on
// each story in both themes.

const meta = {
  title: 'Billing/PriceVersionsTable',
  component: PriceVersionsTable,
  args: {
    versions: PRICE_DETAIL.versions,
    labelledBy: 'versions-title',
    onDelete: fn(),
  },
  render: (args) => (
    <>
      <h2 id="versions-title">{i18n.t('billing.prices.versions.title')}</h2>
      <PriceVersionsTable {...args} />
    </>
  ),
} satisfies Meta<typeof PriceVersionsTable>;

export default meta;
type Story = StoryObj<typeof meta>;

export const WithUpcoming: Story = {
  play: async ({ args, canvas, userEvent }) => {
    for (const status of ['upcoming', 'current', 'past'] as const) {
      await expect(
        canvas.getByText(i18n.t(`billing.prices.status.${status}`)),
      ).toBeVisible();
    }
    const remove = canvas.getAllByRole('button', {
      name: /./,
    });
    await expect(remove).toHaveLength(1);
    await userEvent.click(remove[0]!);
    await expect(args.onDelete).toHaveBeenCalledWith(PRICE_DETAIL.versions[0]);
  },
};

export const NotOffered: Story = {
  args: { versions: [V_NOT_OFFERED] },
  play: async ({ canvas }) => {
    await expect(
      canvas.getByText(i18n.t('billing.prices.notOffered')),
    ).toBeVisible();
    await expect(canvas.queryByRole('button')).toBeNull();
  },
};

export const NoVersions: Story = {
  args: { versions: [] },
  play: async ({ canvas }) => {
    await expect(
      canvas.getByText(i18n.t('billing.prices.versions.empty')),
    ).toBeVisible();
  },
};
