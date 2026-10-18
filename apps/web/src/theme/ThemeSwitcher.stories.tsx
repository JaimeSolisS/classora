import type { Meta, StoryObj } from '@storybook/react-vite';
import { useState } from 'react';
import { expect } from 'storybook/test';
import { i18n } from '@/i18n';
import type { ThemeChoice } from './theme';
import { ThemeSwitcher } from './ThemeSwitcher';

// The story keeps the choice in local state, so it does not change the page
// theme; the toolbar theme stays in control while the story is tested.
function ThemeSwitcherDemo() {
  const [choice, setChoice] = useState<ThemeChoice>('system');
  return <ThemeSwitcher value={choice} onValueChange={setChoice} />;
}

const meta = {
  title: 'Theme/ThemeSwitcher',
  component: ThemeSwitcher,
  args: { value: 'system', onValueChange: () => {} },
} satisfies Meta<typeof ThemeSwitcher>;

export default meta;
type Story = StoryObj<typeof meta>;

// Labels come from the catalogs in the toolbar language.
export const Default: Story = {
  render: () => <ThemeSwitcherDemo />,
  play: async ({ canvas, userEvent }) => {
    const group = canvas.getByRole('group', { name: i18n.t('theme.label') });
    await expect(group).toBeVisible();
    const dark = canvas.getByRole('button', {
      name: i18n.t('theme.options.dark'),
    });
    await userEvent.click(dark);
    await expect(dark).toHaveAttribute('aria-pressed', 'true');
    await expect(
      canvas.getByRole('button', { name: i18n.t('theme.options.system') }),
    ).toHaveAttribute('aria-pressed', 'false');
  },
};

// The same story with the toolbar language set to English.
export const English: Story = {
  ...Default,
  globals: { locale: 'en' },
  play: async (context) => {
    await expect(document.documentElement).toHaveAttribute('lang', 'en');
    await expect(
      context.canvas.getByRole('group', { name: 'Theme' }),
    ).toBeVisible();
    await Default.play?.(context);
  },
};
