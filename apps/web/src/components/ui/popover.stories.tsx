import type { Meta, StoryObj } from '@storybook/react-vite';
import { expect, waitFor, within } from 'storybook/test';
import { Button } from './button';
import { Popover, PopoverContent, PopoverTrigger } from './popover';

const meta = {
  title: 'UI/Popover',
  component: Popover,
} satisfies Meta<typeof Popover>;

export default meta;
type Story = StoryObj<typeof meta>;

export const Default: Story = {
  render: () => (
    <Popover>
      <PopoverTrigger asChild>
        <Button variant="outline">Details</Button>
      </PopoverTrigger>
      <PopoverContent aria-label="Details">
        <h2 className="text-sm font-semibold">Details</h2>
        <p className="mt-2 text-sm">A non-modal panel next to its trigger.</p>
        <Button className="mt-3" size="sm">
          Action
        </Button>
      </PopoverContent>
    </Popover>
  ),
  play: async ({ canvas, canvasElement, userEvent }) => {
    const trigger = canvas.getByRole('button', { name: 'Details' });
    await userEvent.click(trigger);

    // The content is portalled to <body>, outside the story canvas.
    const page = within(canvasElement.ownerDocument.body);
    const panel = await page.findByRole('dialog', { name: 'Details' });
    await expect(panel).toBeVisible();
    await expect(trigger).toHaveAttribute('aria-expanded', 'true');

    await userEvent.keyboard('{Escape}');
    await waitFor(() => expect(page.queryByRole('dialog')).toBeNull());
    await expect(trigger).toHaveFocus();
  },
};
