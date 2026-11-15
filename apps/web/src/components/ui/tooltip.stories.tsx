import type { Meta, StoryObj } from '@storybook/react-vite';
import { House } from 'lucide-react';
import { expect, waitFor, within } from 'storybook/test';
import { Button } from './button';
import {
  Tooltip,
  TooltipContent,
  TooltipProvider,
  TooltipTrigger,
} from './tooltip';

const meta = {
  title: 'UI/Tooltip',
  component: Tooltip,
} satisfies Meta<typeof Tooltip>;

export default meta;
type Story = StoryObj<typeof meta>;

// An icon button whose accessible name the tooltip repeats (#546 B4): it
// opens on keyboard focus and on hover, and Escape dismisses it.
export const Default: Story = {
  render: () => (
    <TooltipProvider>
      <Tooltip>
        <TooltipTrigger asChild>
          <Button variant="outline" size="icon" aria-label="Home">
            <House aria-hidden="true" />
          </Button>
        </TooltipTrigger>
        <TooltipContent side="right">Home</TooltipContent>
      </Tooltip>
    </TooltipProvider>
  ),
  play: async ({ canvas, canvasElement, userEvent }) => {
    const trigger = canvas.getByRole('button', { name: 'Home' });
    // The content is portalled to <body>, outside the story canvas.
    const page = within(canvasElement.ownerDocument.body);

    await userEvent.tab();
    await expect(trigger).toHaveFocus();
    const tooltip = await page.findByRole('tooltip');
    await expect(tooltip).toHaveTextContent('Home');
    // The tooltip doesn't replace the trigger's name.
    await expect(trigger).toHaveAccessibleName('Home');

    await userEvent.keyboard('{Escape}');
    await waitFor(() => expect(page.queryByRole('tooltip')).toBeNull());
    await expect(trigger).toHaveFocus();

    await userEvent.hover(trigger);
    await expect(await page.findByRole('tooltip')).toHaveTextContent('Home');
    await userEvent.unhover(trigger);
  },
};
