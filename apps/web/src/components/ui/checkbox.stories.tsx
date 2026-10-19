import type { Meta, StoryObj } from '@storybook/react-vite';
import { expect, userEvent } from 'storybook/test';
import { Checkbox } from './checkbox';

const meta = {
  title: 'UI/Checkbox',
  component: Checkbox,
} satisfies Meta<typeof Checkbox>;

export default meta;
type Story = StoryObj<typeof meta>;

export const WithLabel: Story = {
  render: () => (
    <div className="flex items-center gap-3">
      <Checkbox id="checkbox-terms" />
      <label htmlFor="checkbox-terms" className="text-sm">
        Send me a copy
      </label>
    </div>
  ),
  play: async ({ canvas }) => {
    const box = canvas.getByRole('checkbox', { name: 'Send me a copy' });
    await expect(box).not.toBeChecked();
    await userEvent.click(canvas.getByText('Send me a copy'));
    await expect(box).toBeChecked();
  },
};

export const Checked: Story = {
  render: () => (
    <div className="flex items-center gap-3">
      <Checkbox id="checkbox-checked" defaultChecked />
      <label htmlFor="checkbox-checked" className="text-sm">
        Tutor
      </label>
    </div>
  ),
};

export const Disabled: Story = {
  render: () => (
    <div className="flex flex-col gap-3">
      <div className="flex items-center gap-3">
        <Checkbox
          id="checkbox-disabled"
          disabled
          aria-describedby="checkbox-disabled-reason"
        />
        <label htmlFor="checkbox-disabled" className="text-sm">
          Owner
        </label>
      </div>
      <div className="flex items-center gap-3">
        <Checkbox id="checkbox-disabled-checked" disabled defaultChecked />
        <label htmlFor="checkbox-disabled-checked" className="text-sm">
          Administrator
        </label>
      </div>
      <p
        id="checkbox-disabled-reason"
        className="text-sm text-muted-foreground"
      >
        You can&apos;t change this role.
      </p>
    </div>
  ),
};

export const Invalid: Story = {
  render: () => (
    <div className="flex items-center gap-3">
      <Checkbox id="checkbox-invalid" aria-invalid="true" />
      <label htmlFor="checkbox-invalid" className="text-sm">
        I agree
      </label>
    </div>
  ),
};
