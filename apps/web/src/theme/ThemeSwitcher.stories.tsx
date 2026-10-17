import type { Meta, StoryObj } from '@storybook/react-vite';
import { useState } from 'react';
import { expect } from 'storybook/test';
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

export const Default: Story = {
  render: () => <ThemeSwitcherDemo />,
  play: async ({ canvas, userEvent }) => {
    const group = canvas.getByRole('group', { name: 'Theme' });
    await expect(group).toBeVisible();
    const dark = canvas.getByRole('button', { name: 'Dark' });
    await userEvent.click(dark);
    await expect(dark).toHaveAttribute('aria-pressed', 'true');
    await expect(
      canvas.getByRole('button', { name: 'System' }),
    ).toHaveAttribute('aria-pressed', 'false');
  },
};
