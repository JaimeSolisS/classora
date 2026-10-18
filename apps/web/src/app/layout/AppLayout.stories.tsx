import type { Meta, StoryObj } from '@storybook/react-vite';
import { expect } from 'storybook/test';
import { i18n } from '@/i18n';
import { resetUiStore } from '@/stores/ui';
import { StoryRouter } from '@/test/StoryRouter';
import { AppLayout } from './AppLayout';

function SamplePage() {
  return <h1 className="text-2xl font-semibold">Classora</h1>;
}

const meta = {
  title: 'App/AppLayout',
  component: AppLayout,
  render: () => (
    <StoryRouter
      routes={[
        {
          path: '/',
          element: <AppLayout />,
          children: [{ index: true, element: <SamplePage /> }],
        },
      ]}
    />
  ),
  parameters: {
    layout: 'fullscreen',
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
  beforeEach: () => {
    resetUiStore();
  },
} satisfies Meta<typeof AppLayout>;

export default meta;
type Story = StoryObj<typeof meta>;

// Desktop (lg and up): navigation and switchers always visible, no toggle.
export const Desktop: Story = {
  play: async ({ canvas }) => {
    await expect(
      canvas.getByRole('navigation', { name: i18n.t('nav.label') }),
    ).toBeVisible();
    await expect(
      canvas.queryByRole('button', { name: i18n.t('nav.toggle') }),
    ).toBeNull();
    await expect(
      canvas.getByRole('group', { name: i18n.t('theme.label') }),
    ).toBeVisible();
    await expect(
      canvas.getByRole('group', { name: i18n.t('language.label') }),
    ).toBeVisible();
  },
};

// Narrow (320 px): navigation and both switchers collapse behind the toggle.
export const Narrow: Story = {
  globals: { viewport: { value: 'narrow320' } },
  play: async ({ canvas, userEvent }) => {
    const toggle = canvas.getByRole('button', { name: i18n.t('nav.toggle') });
    await expect(toggle).toHaveAttribute('aria-expanded', 'false');
    await expect(
      canvas.queryByRole('navigation', { name: i18n.t('nav.label') }),
    ).toBeNull();

    await userEvent.click(toggle);
    await expect(toggle).toHaveAttribute('aria-expanded', 'true');
    await expect(
      canvas.getByRole('navigation', { name: i18n.t('nav.label') }),
    ).toBeVisible();
    await expect(
      canvas.getByRole('group', { name: i18n.t('theme.label') }),
    ).toBeVisible();
    await expect(
      canvas.getByRole('group', { name: i18n.t('language.label') }),
    ).toBeVisible();
    // Nothing overflows horizontally at 320 px.
    await expect(document.documentElement.scrollWidth).toBeLessThanOrEqual(320);

    await userEvent.click(toggle);
    await expect(toggle).toHaveAttribute('aria-expanded', 'false');
  },
};
