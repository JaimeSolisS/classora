import type { Meta, StoryObj } from '@storybook/react-vite';
import { useState } from 'react';
import { expect } from 'storybook/test';
import { i18n } from './index';
import type { Language } from './language';
import { LanguageSwitcher } from './LanguageSwitcher';

// The story keeps the choice in local state, so it does not change the UI
// language; the toolbar language stays in control while the story is tested.
function LanguageSwitcherDemo() {
  const [language, setLanguage] = useState<Language>('es');
  return <LanguageSwitcher value={language} onValueChange={setLanguage} />;
}

const meta = {
  title: 'I18n/LanguageSwitcher',
  component: LanguageSwitcher,
  args: { value: 'es', onValueChange: () => {} },
} satisfies Meta<typeof LanguageSwitcher>;

export default meta;
type Story = StoryObj<typeof meta>;

export const Default: Story = {
  render: () => <LanguageSwitcherDemo />,
  play: async ({ canvas, userEvent }) => {
    const group = canvas.getByRole('group', {
      name: i18n.t('language.label'),
    });
    await expect(group).toBeVisible();
    const spanish = canvas.getByRole('button', { name: 'Español' });
    const english = canvas.getByRole('button', { name: 'English' });
    await expect(spanish).toHaveAttribute('lang', 'es');
    await expect(english).toHaveAttribute('lang', 'en');
    await expect(spanish).toHaveAttribute('aria-pressed', 'true');
    await userEvent.click(english);
    await expect(english).toHaveAttribute('aria-pressed', 'true');
    await expect(spanish).toHaveAttribute('aria-pressed', 'false');
  },
};

// The same story with the toolbar language set to English.
export const English: Story = {
  ...Default,
  globals: { locale: 'en' },
  play: async (context) => {
    await expect(document.documentElement).toHaveAttribute('lang', 'en');
    await expect(
      context.canvas.getByRole('group', { name: 'Language' }),
    ).toBeVisible();
    await Default.play?.(context);
  },
};
