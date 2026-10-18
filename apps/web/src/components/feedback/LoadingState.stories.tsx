import type { Meta, StoryObj } from '@storybook/react-vite';
import { expect } from 'storybook/test';
import { i18n } from '@/i18n';
import { LoadingState } from './LoadingState';

const meta = {
  title: 'Feedback/LoadingState',
  component: LoadingState,
  args: { layout: 'page' },
} satisfies Meta<typeof LoadingState>;

export default meta;
type Story = StoryObj<typeof meta>;

const busyRegion: Story['play'] = async ({ canvas, canvasElement }) => {
  const region = canvasElement.querySelector('[data-slot="loading-state"]');
  await expect(region).toHaveAttribute('aria-busy', 'true');
  await expect(canvas.getByText(i18n.t('feedback.loading'))).toHaveClass(
    'sr-only',
  );
};

export const Page: Story = { play: busyRegion };

export const Table: Story = { args: { layout: 'table' }, play: busyRegion };
