import type { Meta, StoryObj } from '@storybook/react-vite';
import { Input } from './input';

const meta = {
  title: 'UI/Input',
  component: Input,
} satisfies Meta<typeof Input>;

export default meta;
type Story = StoryObj<typeof meta>;

export const WithLabel: Story = {
  render: () => (
    <div className="flex max-w-sm flex-col gap-2">
      <label htmlFor="input-email" className="text-sm font-medium">
        Email
      </label>
      <Input id="input-email" type="email" />
    </div>
  ),
};

export const Disabled: Story = {
  render: () => (
    <div className="flex max-w-sm flex-col gap-2">
      <label htmlFor="input-disabled" className="text-sm font-medium">
        Center name
      </label>
      <Input id="input-disabled" defaultValue="Classora Center" disabled />
    </div>
  ),
};

export const Invalid: Story = {
  render: () => (
    <div className="flex max-w-sm flex-col gap-2">
      <label htmlFor="input-invalid" className="text-sm font-medium">
        Email
      </label>
      <Input
        id="input-invalid"
        type="email"
        defaultValue="not-an-email"
        aria-invalid="true"
        aria-describedby="input-invalid-error"
      />
      <p id="input-invalid-error" className="text-sm text-destructive">
        Enter a valid email address.
      </p>
    </div>
  ),
};
