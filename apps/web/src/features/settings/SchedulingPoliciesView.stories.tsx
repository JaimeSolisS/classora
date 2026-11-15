import type { Meta, StoryObj } from '@storybook/react-vite';
import { expect, fn, waitFor } from 'storybook/test';
import { ApiError } from '@/app/api';
import { i18n } from '@/i18n';
import type { LocalDate } from '@/lib/time';
import { StoryRouter } from '@/test/StoryRouter';
import { OVERVIEW_V1, OVERVIEW_WITH_UPCOMING } from './fixtures';
import { SchedulingPoliciesView } from './SchedulingPoliciesPage';

// The scheduling policies page without its query (#61 F4): loaded with
// upcoming versions, with no upcoming version, loading, a failed load and
// the form with validation errors. No request: every call is a stub. axe
// runs on each story in both themes.

const meta = {
  title: 'Settings/SchedulingPoliciesPage',
  component: SchedulingPoliciesView,
  args: {
    state: { status: 'ready', overview: OVERVIEW_WITH_UPCOMING },
    today: '2026-10-20' as LocalDate,
    timeZone: 'America/Mexico_City',
    onCreate: fn(() => Promise.resolve()),
    onDelete: fn(() => Promise.resolve()),
    onReload: fn(() => Promise.resolve()),
  },
  render: (args) => (
    <StoryRouter
      path="/settings/scheduling-policies"
      routes={[{ path: '*', element: <SchedulingPoliciesView {...args} /> }]}
    />
  ),
} satisfies Meta<typeof SchedulingPoliciesView>;

export default meta;
type Story = StoryObj<typeof meta>;

export const WithUpcoming: Story = {
  play: async ({ canvas }) => {
    await expect(
      canvas.getByRole('heading', {
        level: 1,
        name: i18n.t('settings.schedulingPolicies.title'),
      }),
    ).toBeVisible();
    await expect(
      canvas.getByRole('link', {
        name: i18n.t('settings.tabs.schedulingPolicies'),
      }),
    ).toHaveAttribute('aria-current', 'page');
    await expect(
      canvas.getByRole('table', {
        name: i18n.t('settings.schedulingPolicies.upcoming.title'),
      }),
    ).toBeVisible();
    await expect(
      canvas.getByLabelText(
        i18n.t('settings.schedulingPolicies.fields.changeNoticeHours'),
      ),
    ).toHaveValue(12);
  },
};

export const EmptyUpcoming: Story = {
  args: { state: { status: 'ready', overview: OVERVIEW_V1 } },
  play: async ({ canvas }) => {
    await expect(
      canvas.getByText(i18n.t('settings.schedulingPolicies.upcoming.empty')),
    ).toBeVisible();
    await expect(
      canvas.getByRole('button', {
        name: i18n.t('settings.schedulingPolicies.form.save'),
      }),
    ).toBeDisabled();
  },
};

export const Loading: Story = {
  args: { state: { status: 'loading' } },
  play: async ({ canvasElement }) => {
    await expect(
      canvasElement.querySelector('[aria-busy="true"]'),
    ).not.toBeNull();
  },
};

export const LoadError: Story = {
  args: {
    state: {
      status: 'error',
      error: new ApiError({
        status: 503,
        code: 'database_unavailable',
        requestId: 'req-story',
      }),
      onRetry: fn(),
    },
  },
  play: async ({ canvas }) => {
    await expect(canvas.getByRole('alert')).toBeVisible();
  },
};

export const ValidationErrors: Story = {
  args: { state: { status: 'ready', overview: OVERVIEW_V1 } },
  play: async ({ canvas, userEvent }) => {
    const notice = canvas.getByLabelText(
      i18n.t('settings.schedulingPolicies.fields.changeNoticeHours'),
    );
    await userEvent.clear(notice);
    await userEvent.type(notice, '400');
    await userEvent.click(
      canvas.getByLabelText(
        i18n.t('settings.schedulingPolicies.takesEffect.date'),
      ),
    );
    await userEvent.click(
      canvas.getByRole('button', {
        name: i18n.t('settings.schedulingPolicies.form.save'),
      }),
    );
    await waitFor(() => expect(notice).toHaveAttribute('aria-invalid', 'true'));
    await expect(
      canvas.getByText(
        i18n.t('settings.schedulingPolicies.validation.noticeRange'),
      ),
    ).toBeVisible();
    await expect(
      canvas.getByText(
        i18n.t('settings.schedulingPolicies.validation.dateRequired'),
      ),
    ).toBeVisible();
  },
};
