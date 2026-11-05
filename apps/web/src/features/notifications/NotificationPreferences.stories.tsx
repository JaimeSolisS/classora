import type { Meta, StoryObj } from '@storybook/react-vite';
import { useState } from 'react';
import { expect, within } from 'storybook/test';
import { ApiError } from '@/app/api';
import { noSideScroll, VIEWPORTS } from '@/features/assignments/storyKit';
import { i18n } from '@/i18n';
import {
  GUARDIAN_CATALOGUE,
  OWNER_CATALOGUE,
  TUTOR_CATALOGUE,
} from './preferenceFixtures';
import type { NotificationPreferenceItem } from './preferences-api';
import { preferenceLabelKey } from './preferenceTypes';
import {
  NotificationPreferencesView,
  type PreferencesState,
} from './NotificationPreferencesView';

// M8-02b E2: the preferences page for a guardian, a tutor and the Owner,
// the error state and the phone widths (fixed data: no request). A toggle
// flips the item locally, as a successful save would.

function Preferences({
  items,
  state,
  saveFailed = false,
}: {
  items?: NotificationPreferenceItem[];
  state?: PreferencesState;
  saveFailed?: boolean;
}) {
  const [current, setCurrent] = useState(items ?? []);
  const [announcement, setAnnouncement] = useState('');
  const shown: PreferencesState = state ?? {
    status: 'ready',
    items: current,
    saving: new Map(),
    onToggle: (type, enabled) => {
      setCurrent((previous) =>
        previous.map((item) =>
          item.notification_type === type ? { ...item, enabled } : item,
        ),
      );
      setAnnouncement(i18n.t('notifications.preferences.saved'));
    },
  };
  return (
    <div className="p-4">
      <NotificationPreferencesView
        state={shown}
        announcement={announcement}
        saveError={
          saveFailed
            ? {
                error: new ApiError({
                  code: 'internal_error',
                  status: 500,
                  requestId: 'req-preferences-0001',
                }),
                onRetry: () => {},
              }
            : null
        }
      />
    </div>
  );
}

const meta = {
  title: 'Features/Notifications/NotificationPreferences',
  component: Preferences,
  args: { items: GUARDIAN_CATALOGUE },
  parameters: { layout: 'fullscreen', viewport: { options: VIEWPORTS } },
} satisfies Meta<typeof Preferences>;

export default meta;
type Story = StoryObj<typeof meta>;

// A dynamic key: the typed t() can't check it.
const label = (code: string) =>
  (i18n.t as unknown as (key: string) => string)(preferenceLabelKey(code));

// The guardian: Agenda first with the locked class changes, the muted
// assignment type unchecked; Space turns it on and "Saved." is announced.
export const Guardian: Story = {
  play: async ({ canvasElement, userEvent }) => {
    const canvas = within(canvasElement);
    const headings = canvas
      .getAllByRole('heading', { level: 2 })
      .map((heading) => heading.textContent);
    await expect(headings).toEqual([
      i18n.t('notifications.preferences.groups.scheduling'),
      i18n.t('notifications.preferences.groups.academics'),
      i18n.t('notifications.preferences.groups.billing'),
    ]);
    const cancelled = canvas.getByRole('checkbox', {
      name: label('scheduling.session_cancelled'),
    });
    await expect(cancelled).toBeChecked();
    await expect(cancelled).toBeDisabled();
    await expect(cancelled).toHaveAccessibleDescription(
      i18n.t('notifications.preferences.mandatory'),
    );
    const assignments = canvas.getByRole('checkbox', {
      name: label('academics.assignment_set'),
    });
    await expect(assignments).not.toBeChecked();
    assignments.focus();
    await userEvent.keyboard(' ');
    await expect(assignments).toBeChecked();
    await expect(canvas.getByRole('status')).toHaveTextContent(
      i18n.t('notifications.preferences.saved'),
    );
  },
};

export const Tutor: Story = {
  args: { items: TUTOR_CATALOGUE },
  play: async ({ canvas }) => {
    await expect(
      canvas.getByRole('checkbox', {
        name: label('payroll.period_finalized'),
      }),
    ).toBeChecked();
    await expect(
      canvas.getByRole('checkbox', {
        name: label('academics.progress_report_due'),
      }),
    ).not.toBeChecked();
  },
};

export const Owner: Story = {
  args: { items: OWNER_CATALOGUE },
  play: async ({ canvas }) => {
    await expect(
      canvas.getByRole('checkbox', {
        name: label('administration.approval_assigned'),
      }),
    ).toBeChecked();
    await expect(
      canvas.queryByText(i18n.t('notifications.preferences.mandatoryIntro')),
    ).toBeNull();
  },
};

// A type the web has no label for: the generic text and its code.
export const UnknownType: Story = {
  args: {
    items: [
      ...OWNER_CATALOGUE,
      {
        notification_type: 'security.new_sign_in',
        mandatory: false,
        enabled: true,
      },
    ],
  },
  play: async ({ canvas }) => {
    await expect(
      canvas.getByText(i18n.t('notifications.preferences.groups.other')),
    ).toBeVisible();
    await expect(canvas.getByText('security.new_sign_in')).toBeVisible();
  },
};

export const SaveFailed: Story = {
  args: { items: TUTOR_CATALOGUE, saveFailed: true },
  play: async ({ canvas }) => {
    await expect(canvas.getByRole('alert')).toHaveTextContent(
      i18n.t('notifications.preferences.saveFailed'),
    );
  },
};

export const Empty: Story = {
  args: { items: [] },
  play: async ({ canvas }) => {
    await expect(
      canvas.getByText(i18n.t('notifications.preferences.empty')),
    ).toBeVisible();
  },
};

export const Loading: Story = {
  args: { state: { status: 'loading' } },
};

export const LoadError: Story = {
  args: {
    state: {
      status: 'error',
      error: new ApiError({
        code: 'internal_error',
        status: 500,
        requestId: 'req-preferences-0002',
      }),
      onRetry: () => {},
    },
  },
  play: async ({ canvas }) => {
    await expect(canvas.getByRole('alert')).toHaveTextContent(
      'req-preferences-0002',
    );
  },
};

export const GuardianNarrow320: Story = {
  args: { items: GUARDIAN_CATALOGUE },
  globals: { viewport: { value: 'narrow320' } },
  play: async ({ canvasElement }) => {
    await noSideScroll(canvasElement);
  },
};

export const TutorPhone390: Story = {
  args: { items: TUTOR_CATALOGUE },
  globals: { viewport: { value: 'phone390' } },
  play: async ({ canvasElement }) => {
    await noSideScroll(canvasElement);
  },
};
