import type { Meta, StoryObj } from '@storybook/react-vite';
import { Skeleton } from './skeleton';

const meta = {
  title: 'UI/Skeleton',
  component: Skeleton,
} satisfies Meta<typeof Skeleton>;

export default meta;
type Story = StoryObj<typeof meta>;

// The skeleton shapes are hidden from assistive technology; the region is
// marked busy and has a visually hidden loading message instead.
export const Loading: Story = {
  render: () => (
    <div aria-busy="true" className="flex max-w-sm items-center gap-4">
      <span className="sr-only">Loading profile…</span>
      <Skeleton className="size-12 rounded-full" />
      <div className="flex flex-1 flex-col gap-2">
        <Skeleton className="h-4 w-3/4" />
        <Skeleton className="h-4 w-1/2" />
      </div>
    </div>
  ),
};
