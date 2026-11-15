import type { Meta, StoryObj } from '@storybook/react-vite';
import { expect, fn } from 'storybook/test';
import { i18n } from '@/i18n';
import { StoryRouter } from '@/test/StoryRouter';
import { LAURA, LAURA_OWN_VIEW } from './fixtures';
import { GuardianDetailView } from './GuardianDetailPage';

// The guardian detail (#34 I3, J3): staff with `account` and the
// guardian's own view (`account: null`). No request.

const meta = {
  title: 'Guardians/GuardianDetailView',
  component: GuardianDetailView,
  args: {
    guardian: LAURA,
    canManage: true,
    onSetActive: fn(() => Promise.resolve()),
    onReload: fn(() => Promise.resolve()),
  },
  render: (args) => (
    <StoryRouter
      routes={[{ path: '*', element: <GuardianDetailView {...args} /> }]}
    />
  ),
} satisfies Meta<typeof GuardianDetailView>;

export default meta;
type Story = StoryObj<typeof meta>;

export const Staff: Story = {
  play: async ({ canvas }) => {
    await expect(
      canvas.getByRole('heading', { level: 1, name: 'Laura Ñúñez Prieto' }),
    ).toBeVisible();
    await expect(canvas.getByText('laura.nunez')).toBeVisible();
    await expect(
      canvas.getByRole('link', { name: 'José Ñúñez Pérez' }),
    ).toBeVisible();
    await expect(
      canvas.getByRole('button', {
        name: i18n.t('guardians.deactivate.action'),
      }),
    ).toBeVisible();
  },
};

export const OwnView: Story = {
  args: { guardian: LAURA_OWN_VIEW, canManage: false },
  play: async ({ canvas, canvasElement }) => {
    await expect(canvasElement.textContent).not.toContain('laura.nunez');
    await expect(
      canvas.getByText(i18n.t('guardians.account.has')),
    ).toBeVisible();
    await expect(canvas.queryByRole('button')).toBeNull();
  },
};
