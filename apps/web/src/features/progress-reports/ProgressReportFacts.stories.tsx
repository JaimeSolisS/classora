import type { Meta, StoryObj } from '@storybook/react-vite';
import { expect, within } from 'storybook/test';
import { i18n } from '@/i18n';
import type { ReportFacts } from './api';
import { EMPTY_FACTS, FACTS, TRUNCATED_FACTS } from './fixtures';
import { ProgressReportFacts } from './ProgressReportFacts';
import { noSideScroll, VIEWPORTS } from './storyKit';

// The facts view (#315 H2): full, zero data, truncated and an unsupported
// schema version. Text first; the plan bar is decorative. Every story has
// 320 and 390 px variants that assert the page never scrolls sideways. Axe
// runs in light and dark.

function FactsStory({ facts }: { facts: ReportFacts }) {
  return (
    <main className="min-w-0 p-4">
      <h1 className="sr-only">Facts</h1>
      <ProgressReportFacts facts={facts} heading="Facts as approved" />
    </main>
  );
}

const meta = {
  title: 'Progress reports/Facts',
  component: FactsStory,
  args: { facts: FACTS },
  parameters: { viewport: { options: VIEWPORTS } },
} satisfies Meta<typeof FactsStory>;

export default meta;
type Story = StoryObj<typeof meta>;

const at = (story: Story, value: keyof typeof VIEWPORTS): Story => ({
  ...story,
  globals: { viewport: { value } },
  play: async (context) => {
    await story.play?.(context);
    await noSideScroll(context.canvasElement);
  },
});

export const Full: Story = {
  play: async ({ canvasElement }) => {
    await expect(
      within(canvasElement).getByText(
        i18n.t('progressReports.facts.plans.progress', { percent: '67' }),
      ),
    ).toBeVisible();
  },
};
export const FullNarrow320 = at(Full, 'narrow320');
export const FullPhone390 = at(Full, 'phone390');

export const ZeroData: Story = {
  args: { facts: EMPTY_FACTS },
  play: async ({ canvasElement }) => {
    await expect(
      within(canvasElement).getByText(
        i18n.t('progressReports.facts.attendance.none'),
      ),
    ).toBeVisible();
  },
};
export const ZeroDataNarrow320 = at(ZeroData, 'narrow320');
export const ZeroDataPhone390 = at(ZeroData, 'phone390');

export const Truncated: Story = {
  args: { facts: TRUNCATED_FACTS },
};
export const TruncatedNarrow320 = at(Truncated, 'narrow320');
export const TruncatedPhone390 = at(Truncated, 'phone390');

export const UnsupportedVersion: Story = {
  args: { facts: { ...FACTS, schema_version: 2 } as unknown as ReportFacts },
  play: async ({ canvasElement }) => {
    await expect(
      within(canvasElement).getByText(
        i18n.t('progressReports.facts.unsupported'),
      ),
    ).toBeVisible();
  },
};
export const UnsupportedVersionNarrow320 = at(UnsupportedVersion, 'narrow320');
export const UnsupportedVersionPhone390 = at(UnsupportedVersion, 'phone390');
