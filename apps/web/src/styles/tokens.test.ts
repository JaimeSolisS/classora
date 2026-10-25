import { describe, expect, it } from 'vitest';
import tokensCss from './tokens.css?raw';

// WCAG 2.2 contrast check of the color tokens. Values are parsed from
// tokens.css, so changing a token re-runs the check against the new value.

const COLOR_TOKENS = [
  'background',
  'foreground',
  'card',
  'card-foreground',
  'popover',
  'popover-foreground',
  'primary',
  'primary-foreground',
  'primary-hover',
  'secondary',
  'secondary-foreground',
  'muted',
  'muted-foreground',
  'accent',
  'accent-foreground',
  'destructive',
  'destructive-foreground',
  'border',
  'input',
  'ring',
] as const;

type ColorToken = (typeof COLOR_TOKENS)[number];

// Text pairs: SC 1.4.3, at least 4.5:1.
const TEXT_PAIRS: [ColorToken, ColorToken][] = [
  ['foreground', 'background'],
  ['card-foreground', 'card'],
  ['popover-foreground', 'popover'],
  ['primary-foreground', 'primary'],
  // The default button's hover background (opaque, so the surface behind
  // the button does not change the ratio).
  ['primary-foreground', 'primary-hover'],
  ['secondary-foreground', 'secondary'],
  ['muted-foreground', 'muted'],
  ['muted-foreground', 'background'],
  ['accent-foreground', 'accent'],
  ['destructive-foreground', 'destructive'],
  // Error messages and link-style text drawn on the page background.
  ['destructive', 'background'],
  ['primary', 'background'],
];

// Non-text pairs: SC 1.4.11, at least 3:1.
const NON_TEXT_PAIRS: [ColorToken, ColorToken][] = [
  ['input', 'background'],
  ['ring', 'background'],
  // The ProgressMeter (#310 B5): the fill (primary) against its track
  // (muted); the track's border is the input/background pair above.
  ['primary', 'muted'],
];

function parseBlock(selector: string): Map<string, string> {
  const escaped = selector.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
  const block = new RegExp(`(?:^|\\n)${escaped}\\s*\\{([^}]*)\\}`).exec(
    tokensCss,
  );
  if (!block?.[1]) {
    throw new Error(`No "${selector}" block in tokens.css`);
  }
  const values = new Map<string, string>();
  for (const match of block[1].matchAll(/--([\w-]+)\s*:\s*([^;]+);/g)) {
    values.set(match[1] ?? '', (match[2] ?? '').trim());
  }
  return values;
}

type Rgb = [number, number, number];

function parseHex(hex: string): Rgb {
  const match = /^#([0-9a-f]{6})$/i.exec(hex);
  if (!match?.[1]) {
    throw new Error(`Expected a #rrggbb color, got "${hex}"`);
  }
  const n = parseInt(match[1], 16);
  return [(n >> 16) & 255, (n >> 8) & 255, n & 255];
}

// Relative luminance, WCAG 2.2 definition.
function luminance(rgb: Rgb): number {
  const [r, g, b] = rgb.map((value) => {
    const c = value / 255;
    return c <= 0.04045 ? c / 12.92 : ((c + 0.055) / 1.055) ** 2.4;
  });
  return 0.2126 * (r ?? 0) + 0.7152 * (g ?? 0) + 0.0722 * (b ?? 0);
}

function contrastRatio(a: Rgb, b: Rgb): number {
  const [hi, lo] = [luminance(a), luminance(b)].sort((x, y) => y - x);
  return ((hi ?? 0) + 0.05) / ((lo ?? 0) + 0.05);
}

const themes = {
  light: parseBlock(':root'),
  dark: parseBlock('.dark'),
};

describe('contrast ratio helper', () => {
  it('matches the WCAG reference values', () => {
    const white: Rgb = [255, 255, 255];
    expect(contrastRatio([0, 0, 0], white)).toBeCloseTo(21, 5);
    expect(contrastRatio([119, 119, 119], white)).toBeCloseTo(4.48, 2);
    expect(contrastRatio(white, white)).toBe(1);
  });
});

describe.each(Object.entries(themes))('%s theme tokens', (_name, tokens) => {
  function color(token: ColorToken): Rgb {
    const value = tokens.get(token);
    if (!value) {
      throw new Error(`Token --${token} is missing`);
    }
    return parseHex(value);
  }

  it('defines every semantic color token', () => {
    for (const token of COLOR_TOKENS) {
      expect(tokens.get(token), `--${token}`).toMatch(/^#[0-9a-f]{6}$/i);
    }
  });

  it.each(TEXT_PAIRS)('%s on %s reaches 4.5:1', (fg, bg) => {
    expect(contrastRatio(color(fg), color(bg))).toBeGreaterThanOrEqual(4.5);
  });

  it.each(NON_TEXT_PAIRS)('%s against %s reaches 3:1', (fg, bg) => {
    expect(contrastRatio(color(fg), color(bg))).toBeGreaterThanOrEqual(3);
  });
});
