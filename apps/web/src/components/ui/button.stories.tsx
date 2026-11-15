import type { Meta, StoryObj } from '@storybook/react-vite';
import { expect, within } from 'storybook/test';
import { Button } from './button';

const meta = {
  title: 'UI/Button',
  component: Button,
  args: { children: 'Save changes' },
} satisfies Meta<typeof Button>;

export default meta;
type Story = StoryObj<typeof meta>;

export const Default: Story = {};

export const Variants: Story = {
  render: (args) => (
    <div className="flex flex-wrap items-center gap-2">
      <Button {...args} variant="default">
        Default
      </Button>
      <Button {...args} variant="secondary">
        Secondary
      </Button>
      <Button {...args} variant="destructive">
        Destructive
      </Button>
      <Button {...args} variant="outline">
        Outline
      </Button>
      <Button {...args} variant="ghost">
        Ghost
      </Button>
      <Button {...args} variant="link">
        Link
      </Button>
    </div>
  ),
};

export const Sizes: Story = {
  render: (args) => (
    <div className="flex flex-wrap items-center gap-2">
      <Button {...args} size="xs">
        Extra small
      </Button>
      <Button {...args} size="sm">
        Small
      </Button>
      <Button {...args} size="default">
        Default
      </Button>
      <Button {...args} size="lg">
        Large
      </Button>
      <Button {...args} size="icon-xs" aria-label="Add (extra small)">
        +
      </Button>
      <Button {...args} size="icon-sm" aria-label="Add (small)">
        +
      </Button>
      <Button {...args} size="icon" aria-label="Add">
        +
      </Button>
      <Button {...args} size="icon-lg" aria-label="Add (large)">
        +
      </Button>
    </div>
  ),
};

export const Disabled: Story = {
  args: { disabled: true },
};

// '#rrggbb' -> 'rgb(r, g, b)', the form getComputedStyle returns.
function hexToRgb(hex: string): string {
  const match = /^#([0-9a-f]{6})$/i.exec(hex.trim());
  if (!match?.[1]) {
    throw new Error(`Expected a #rrggbb color, got "${hex}"`);
  }
  const n = parseInt(match[1], 16);
  return `rgb(${(n >> 16) & 255}, ${(n >> 8) & 255}, ${n & 255})`;
}

// The default button in its hover state, so the axe check (light and dark)
// covers primary-foreground on primary-hover (#273). The pointer really
// moves over the button (Playwright through vitest/browser): Tailwind puts
// hover rules inside @media (hover: hover), and storybook-addon-pseudo-states
// does not rewrite them under the Vitest runner. vitest/browser throws
// outside Vitest's browser mode, so it is imported only there; in
// `make storybook` the story renders the resting button (hover it by hand).
// The colour assertion proves the hover applied, so axe never passes on the
// resting colours by accident.
export const PrimaryHover: Story = {
  args: { variant: 'default' },
  play: async ({ canvasElement }) => {
    if (!('__vitest_browser__' in globalThis)) {
      return;
    }
    const { userEvent } = await import('vitest/browser');
    const button = within(canvasElement).getByRole('button', {
      name: 'Save changes',
    });
    await userEvent.hover(button);
    // The button has transition-all: wait until the colour settles.
    await Promise.all(button.getAnimations().map((a) => a.finished));
    const hover = getComputedStyle(document.documentElement).getPropertyValue(
      '--primary-hover',
    );
    await expect(getComputedStyle(button).backgroundColor).toBe(
      hexToRgb(hover),
    );
  },
};
