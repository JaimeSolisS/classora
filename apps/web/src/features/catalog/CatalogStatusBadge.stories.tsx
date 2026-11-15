import type { Meta, StoryObj } from '@storybook/react-vite';
import { expect } from 'storybook/test';
import { i18n } from '@/i18n';
import { CatalogStatusBadge } from './CatalogStatusBadge';

// Active / Inactive (#32 H2): text, never colour alone.

const meta = {
  title: 'Catalog/CatalogStatusBadge',
  component: CatalogStatusBadge,
  args: { active: true },
  render: () => (
    <div className="flex flex-wrap gap-2">
      <CatalogStatusBadge active />
      <CatalogStatusBadge active={false} />
    </div>
  ),
} satisfies Meta<typeof CatalogStatusBadge>;

export default meta;
type Story = StoryObj<typeof meta>;

export const BothStatuses: Story = {
  play: async ({ canvas }) => {
    await expect(
      canvas.getByText(i18n.t('catalog.status.active')),
    ).toBeVisible();
    await expect(
      canvas.getByText(i18n.t('catalog.status.inactive')),
    ).toBeVisible();
  },
};
