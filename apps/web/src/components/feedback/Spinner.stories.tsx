import type { Meta, StoryObj } from '@storybook/react-vite';
import { expect } from 'storybook/test';
import { i18n } from '@/i18n';
import { Spinner } from './Spinner';

const meta = {
  title: 'Feedback/Spinner',
  component: Spinner,
} satisfies Meta<typeof Spinner>;

export default meta;
type Story = StoryObj<typeof meta>;

// Decorative: hidden from assistive technology.
export const Default: Story = {
  play: async ({ canvasElement }) => {
    const spinner = canvasElement.querySelector('[data-slot="spinner"]');
    await expect(spinner).toHaveAttribute('aria-hidden', 'true');
  },
};

// With a visible label (from the catalogs, in the toolbar language).
export const WithLabel: Story = {
  render: () => <Spinner label={i18n.t('feedback.loading')} showLabel />,
  play: async ({ canvas }) => {
    await expect(canvas.getByText(i18n.t('feedback.loading'))).toBeVisible();
  },
};
