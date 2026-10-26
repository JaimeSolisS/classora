import type { Meta, StoryObj } from '@storybook/react-vite';
import { expect, userEvent, within } from 'storybook/test';
import { i18n } from '@/i18n';
import { StoryAuth } from '@/test/StoryAuth';
import type { StudentProgress } from './api';
import { AttendanceChart } from './charts/AttendanceChart';
import { GradeTrendChart } from './charts/GradeTrendChart';
import { PROGRESS } from './fixtures';
import { guardianSession, noSideScroll, VIEWPORTS } from './storyKit';

// The progress charts (#322 G2): each chart in Chart view and in Table
// view (the play function clicks "Table" and checks the scroll region's
// role, name and tabindex), the grade trend with the homework series, at
// 320, 390 and 1280 px; the narrow variants assert no sideways scroll. Axe
// runs in light and dark.

function Charts({
  chart,
  progress = PROGRESS,
  homework = false,
}: {
  chart: 'attendance' | 'grades';
  progress?: StudentProgress;
  homework?: boolean;
}) {
  return (
    <StoryAuth session={guardianSession}>
      <main className="min-w-0 p-4">
        {chart === 'attendance' ? (
          <AttendanceChart progress={progress} />
        ) : (
          <GradeTrendChart progress={progress} initialHomework={homework} />
        )}
      </main>
    </StoryAuth>
  );
}

const meta = {
  title: 'Progress/Charts',
  component: Charts,
  args: { chart: 'attendance' },
  parameters: { viewport: { options: VIEWPORTS } },
} satisfies Meta<typeof Charts>;

export default meta;
type Story = StoryObj<typeof meta>;

const at = (story: Story, value: keyof typeof VIEWPORTS): Story => ({
  ...story,
  globals: { viewport: { value } },
  play: async (context) => {
    await story.play?.(context);
    if (value !== 'desktop1280') {
      await noSideScroll(context.canvasElement);
    }
  },
});

async function toTable(canvasElement: HTMLElement, caption: string) {
  const canvas = within(canvasElement);
  const table = canvas.getByRole('button', {
    name: i18n.t('progress.chart.table'),
  });
  await userEvent.click(table);
  await expect(table).toHaveAttribute('aria-pressed', 'true');
  const region = canvas.getByRole('region', { name: caption });
  await expect(region).toHaveAttribute('tabindex', '0');
  await expect(within(region).getByRole('table')).toBeVisible();
}

export const Attendance: Story = {
  play: async ({ canvasElement }) => {
    await expect(
      canvasElement.querySelector('[data-chart="attendance"] svg'),
    ).not.toBeNull();
  },
};
export const AttendanceNarrow320 = at(Attendance, 'narrow320');
export const AttendancePhone390 = at(Attendance, 'phone390');
export const AttendanceDesktop1280 = at(Attendance, 'desktop1280');

export const AttendanceTable: Story = {
  play: ({ canvasElement }) =>
    toTable(canvasElement, i18n.t('progress.attendance.caption')),
};
export const AttendanceTableNarrow320 = at(AttendanceTable, 'narrow320');
export const AttendanceTablePhone390 = at(AttendanceTable, 'phone390');
export const AttendanceTableDesktop1280 = at(AttendanceTable, 'desktop1280');

export const GradeTrend: Story = {
  args: { chart: 'grades' },
  play: async ({ canvasElement }) => {
    await expect(
      canvasElement.querySelector('[data-chart="grades"] svg'),
    ).not.toBeNull();
  },
};
export const GradeTrendNarrow320 = at(GradeTrend, 'narrow320');
export const GradeTrendPhone390 = at(GradeTrend, 'phone390');
export const GradeTrendDesktop1280 = at(GradeTrend, 'desktop1280');

export const GradeTrendWithHomework: Story = {
  args: { chart: 'grades', homework: true },
  play: async ({ canvasElement }) => {
    await expect(
      within(canvasElement).getByRole('button', {
        name: i18n.t('progress.grades.showHomework'),
      }),
    ).toHaveAttribute('aria-pressed', 'true');
  },
};
export const GradeTrendWithHomeworkNarrow320 = at(
  GradeTrendWithHomework,
  'narrow320',
);
export const GradeTrendWithHomeworkPhone390 = at(
  GradeTrendWithHomework,
  'phone390',
);
export const GradeTrendWithHomeworkDesktop1280 = at(
  GradeTrendWithHomework,
  'desktop1280',
);

export const GradeTrendTable: Story = {
  args: { chart: 'grades' },
  play: ({ canvasElement }) =>
    toTable(canvasElement, i18n.t('progress.grades.caption')),
};
export const GradeTrendTableNarrow320 = at(GradeTrendTable, 'narrow320');
export const GradeTrendTablePhone390 = at(GradeTrendTable, 'phone390');
export const GradeTrendTableDesktop1280 = at(GradeTrendTable, 'desktop1280');
