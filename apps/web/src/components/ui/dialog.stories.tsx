import type { Meta, StoryObj } from '@storybook/react-vite';
import { expect, waitFor, within } from 'storybook/test';
import { Button } from './button';
import {
  Dialog,
  DialogClose,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
  DialogTrigger,
} from './dialog';

const meta = {
  title: 'UI/Dialog',
  component: Dialog,
} satisfies Meta<typeof Dialog>;

export default meta;
type Story = StoryObj<typeof meta>;

export const Default: Story = {
  render: () => (
    <Dialog>
      <DialogTrigger asChild>
        <Button variant="outline">Edit profile</Button>
      </DialogTrigger>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>Edit profile</DialogTitle>
          <DialogDescription>
            Change your display name. Your changes are saved when you select
            Save.
          </DialogDescription>
        </DialogHeader>
        <DialogFooter>
          <DialogClose asChild>
            <Button variant="outline">Cancel</Button>
          </DialogClose>
          <Button>Save</Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  ),
  play: async ({ canvas, canvasElement, userEvent }) => {
    const trigger = canvas.getByRole('button', { name: 'Edit profile' });
    await userEvent.click(trigger);

    // The dialog is portalled to <body>, outside the story canvas.
    const page = within(canvasElement.ownerDocument.body);
    const dialog = await page.findByRole('dialog', { name: 'Edit profile' });
    await expect(dialog).toBeVisible();
    await expect(dialog).toHaveAccessibleDescription(
      /Change your display name/,
    );

    await userEvent.keyboard('{Escape}');
    await waitFor(() => expect(page.queryByRole('dialog')).toBeNull());
    await expect(trigger).toHaveFocus();
  },
};
