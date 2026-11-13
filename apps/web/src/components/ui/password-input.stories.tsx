import type { Meta, StoryObj } from '@storybook/react-vite';
import { expect } from 'storybook/test';
import { i18n } from '@/i18n';
import { PasswordInput } from './password-input';

// #664: the password field with its show/hide toggle, in each state.

const meta = {
  title: 'UI/PasswordInput',
  component: PasswordInput,
} satisfies Meta<typeof PasswordInput>;

export default meta;
type Story = StoryObj<typeof meta>;

const field = (root: HTMLElement) =>
  root.querySelector<HTMLInputElement>('input')!;
const toggle = (root: HTMLElement) =>
  root.querySelector<HTMLButtonElement>('button[aria-controls]')!;

/** Hidden, as every field starts. */
export const Hidden: Story = {
  render: () => (
    <div className="flex max-w-sm flex-col gap-2">
      <label htmlFor="password-hidden" className="text-sm font-medium">
        Password
      </label>
      <PasswordInput
        id="password-hidden"
        autoComplete="current-password"
        defaultValue="correct-horse-battery-staple"
      />
    </div>
  ),
  play: async ({ canvasElement, userEvent }) => {
    const input = field(canvasElement);
    const button = toggle(canvasElement);
    await expect(input).toHaveAttribute('type', 'password');
    await expect(button).toHaveAccessibleName(i18n.t('common.showPassword'));
    await expect(button).toHaveAttribute('aria-controls', input.id);
    await expect(input).toHaveAccessibleName('Password');
    // The pointer target is at least 24 × 24 CSS px (SC 2.5.8).
    const box = button.getBoundingClientRect();
    await expect(box.width).toBeGreaterThanOrEqual(24);
    await expect(box.height).toBeGreaterThanOrEqual(24);

    await userEvent.click(button);
    await expect(input).toHaveAttribute('type', 'text');
    await expect(button).toHaveAccessibleName(i18n.t('common.hidePassword'));
    await expect(button).toHaveFocus();

    await userEvent.keyboard('{Enter}');
    await expect(input).toHaveAttribute('type', 'password');
    await userEvent.keyboard(' ');
    await expect(input).toHaveAttribute('type', 'text');
    await expect(input).toHaveValue('correct-horse-battery-staple');
    await expect(input).toHaveAttribute('autocomplete', 'current-password');
  },
};

/** Shown after the toggle, for the axe check of that state. */
export const Shown: Story = {
  render: Hidden.render,
  play: async ({ canvasElement, userEvent }) => {
    await userEvent.click(toggle(canvasElement));
    await expect(field(canvasElement)).toHaveAttribute('type', 'text');
    await expect(toggle(canvasElement)).toHaveAccessibleName(
      i18n.t('common.hidePassword'),
    );
  },
};

/** With a field error linked by `aria-describedby`. */
export const WithError: Story = {
  render: () => (
    <div className="flex max-w-sm flex-col gap-2">
      <label htmlFor="password-error" className="text-sm font-medium">
        New password
      </label>
      <PasswordInput
        id="password-error"
        autoComplete="new-password"
        defaultValue="short"
        aria-invalid="true"
        aria-describedby="password-error-text"
      />
      <p id="password-error-text" className="text-sm text-destructive">
        Use at least 12 characters.
      </p>
    </div>
  ),
  play: async ({ canvasElement, userEvent }) => {
    const input = field(canvasElement);
    await expect(input).toHaveAccessibleDescription(
      'Use at least 12 characters.',
    );
    await userEvent.click(toggle(canvasElement));
    await expect(input).toHaveAttribute('type', 'text');
    await expect(input).toHaveAttribute('aria-invalid', 'true');
    await expect(input).toHaveAccessibleDescription(
      'Use at least 12 characters.',
    );
  },
};

/** A disabled field disables its toggle too. */
export const Disabled: Story = {
  render: () => (
    <div className="flex max-w-sm flex-col gap-2">
      <label htmlFor="password-disabled" className="text-sm font-medium">
        Password
      </label>
      <PasswordInput
        id="password-disabled"
        autoComplete="current-password"
        defaultValue="correct-horse-battery-staple"
        disabled
      />
    </div>
  ),
  play: async ({ canvasElement }) => {
    await expect(toggle(canvasElement)).toBeDisabled();
    await expect(field(canvasElement)).toHaveAttribute('type', 'password');
  },
};
