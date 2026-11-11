import type { Meta, StoryObj } from '@storybook/react-vite';
import { useState } from 'react';
import { expect, waitFor, within } from 'storybook/test';
import { TimeField, type TimeFieldProps } from './time-field';

// The type-or-pick time field (HF-13, #598 E1 a, AC5, AC9): the quarter-hour
// list in the app language (es by default, the locale toolbar), typing,
// a time off the grid, an error, and a 320 px viewport. Axe runs in light
// and dark (the storybook projects). The label and error are the caller's,
// as on "Schedule a session". Disabled while a dialog sends (#600).

function Field({
  initial = '',
  error,
  ...rest
}: Partial<TimeFieldProps> & { initial?: string; error?: string }) {
  const [value, setValue] = useState(initial);
  return (
    <div className="flex max-w-xs flex-col gap-2">
      <label
        id="story-start-label"
        htmlFor="story-start"
        className="text-sm font-medium"
      >
        Start
      </label>
      <TimeField
        id="story-start"
        labelId="story-start-label"
        value={value}
        onChange={(next) => setValue(next)}
        invalid={error !== undefined}
        aria-describedby={error ? 'story-start-error' : undefined}
        {...rest}
      />
      {error ? (
        <p id="story-start-error" className="text-sm text-destructive">
          {error}
        </p>
      ) : null}
      <output className="text-sm text-muted-foreground" data-testid="value">
        {value}
      </output>
    </div>
  );
}

const meta = {
  title: 'UI/TimeField',
  component: Field,
  parameters: {
    viewport: {
      options: {
        narrow320: {
          name: 'Narrow (320 px)',
          styles: { width: '320px', height: '640px' },
          type: 'mobile',
        },
      },
    },
  },
} satisfies Meta<typeof Field>;

export default meta;
type Story = StoryObj<typeof meta>;

/** The portalled list, outside the story canvas. */
function page(canvasElement: HTMLElement) {
  return within(canvasElement.ownerDocument.body);
}

export const Empty: Story = {
  play: async ({ canvas }) => {
    const field = canvas.getByRole('combobox', { name: 'Start' });
    await expect(field).toHaveAttribute('aria-expanded', 'false');
  },
};

export const OpenList: Story = {
  args: { initial: '09:30' },
  play: async ({ canvas, canvasElement, userEvent }) => {
    const field = canvas.getByRole('combobox', { name: 'Start' });
    await userEvent.click(field);
    const list = await page(canvasElement).findByRole('listbox', {
      name: 'Start',
    });
    await waitFor(() =>
      expect(within(list).getAllByRole('option')).toHaveLength(96),
    );
    await expect(field).toHaveAttribute('aria-expanded', 'true');
    await expect(
      within(list).getByRole('option', { selected: true }),
    ).toBeInTheDocument();
  },
};

export const Typed: Story = {
  play: async ({ canvas, userEvent }) => {
    const field = canvas.getByRole('combobox', { name: 'Start' });
    await userEvent.type(field, '930');
    await userEvent.tab();
    await expect(canvas.getByTestId('value')).toHaveTextContent('09:30');
  },
};

export const OffTheGrid: Story = {
  args: { initial: '10:10' },
};

export const WithError: Story = {
  args: { error: 'Enter the start time.' },
  play: async ({ canvas }) => {
    const field = canvas.getByRole('combobox', { name: 'Start' });
    await expect(field).toHaveAttribute('aria-invalid', 'true');
  },
};

export const Narrow320: Story = {
  args: { initial: '16:45' },
  globals: { viewport: { value: 'narrow320' } },
  play: async ({ canvas, canvasElement, userEvent }) => {
    await userEvent.click(canvas.getByRole('combobox', { name: 'Start' }));
    await page(canvasElement).findByRole('listbox');
    const root = canvasElement.ownerDocument.documentElement;
    await expect(root.scrollWidth).toBeLessThanOrEqual(root.clientWidth);
  },
};

/** #600 AC7: while a dialog sends, the field and its button are disabled. */
export const Disabled: Story = {
  args: { initial: '10:00', disabled: true },
  play: async ({ canvas, canvasElement }) => {
    const field = canvas.getByRole('combobox', { name: 'Start' });
    await expect(field).toBeDisabled();
    await expect(field).toHaveValue('10:00');
    await expect(
      canvas.getByRole('button', { hidden: true, name: /./ }),
    ).toBeDisabled();
    await expect(page(canvasElement).queryByRole('listbox')).toBeNull();
  },
};
