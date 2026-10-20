import type { Meta, StoryObj } from '@storybook/react-vite';
import { expect } from 'storybook/test';
import { ApiError } from '@/app/api';
import { i18n } from '@/i18n';
import type { Session } from '@/features/auth/session';
import {
  PreferencesContext,
  type PreferencesContextValue,
} from '@/features/preferences/usePreferences';
import { UNREAD_COUNT_KEY } from '@/features/notifications/api';
import { resetUiStore } from '@/stores/ui';
import { StoryAuth } from '@/test/StoryAuth';
import { StoryQueryClient } from '@/test/StoryQueryClient';
import { StoryRouter } from '@/test/StoryRouter';
import type { NavItem } from '../navigation';
import { AppLayout } from './AppLayout';

function SamplePage() {
  return <h1 className="text-2xl font-semibold">Classora</h1>;
}

const signedIn: Session = {
  status: 'signedIn',
  user: {
    id: '00000000-0000-4000-8000-000000000001',
    username: 'demo.owner',
    display_name: 'Demo Owner',
    roles: ['administrator'],
    permissions: ['user.view'],
    privacy_notice_required: false,
    preferences: { language: 'es', theme: 'system' },
    center_time_zone: 'America/Mexico_City',
  },
};

// The header with a session (no request: a fixed auth context, and the
// notification bell's unread count seeded: 2).
function renderLayout(session: Session, navItems?: readonly NavItem[]) {
  return (
    <StoryQueryClient data={[[UNREAD_COUNT_KEY, 2]]}>
      <StoryAuth session={session}>
        <StoryRouter
          routes={[
            {
              path: '/',
              element: <AppLayout navItems={navItems} />,
              children: [{ index: true, element: <SamplePage /> }],
            },
          ]}
        />
      </StoryAuth>
    </StoryQueryClient>
  );
}

// #36 F5: a theme choice that couldn't be saved to the account (a fixed
// preferences context: no request).
const saveFailed: PreferencesContextValue = {
  changeLanguage: () => {},
  changeTheme: () => {},
  failure: {
    error: new ApiError({
      code: 'internal_error',
      status: 500,
      requestId: 'req-preferences-0001',
    }),
    fields: ['theme'],
  },
  retry: () => {},
};

function renderSaveFailed() {
  return (
    <PreferencesContext value={saveFailed}>
      {renderLayout(signedIn)}
    </PreferencesContext>
  );
}

const meta = {
  title: 'App/AppLayout',
  component: AppLayout,
  render: () => renderLayout(signedIn),
  parameters: {
    layout: 'fullscreen',
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
  beforeEach: () => {
    resetUiStore();
  },
} satisfies Meta<typeof AppLayout>;

export default meta;
type Story = StoryObj<typeof meta>;

// Desktop (lg and up), signed in: navigation, switchers, the user name and
// "Sign out" always visible, no toggle.
export const Desktop: Story = {
  play: async ({ canvas }) => {
    await expect(
      canvas.getByRole('navigation', { name: i18n.t('nav.label') }),
    ).toBeVisible();
    await expect(
      canvas.queryByRole('button', { name: i18n.t('nav.toggle') }),
    ).toBeNull();
    await expect(
      canvas.getByRole('group', { name: i18n.t('theme.label') }),
    ).toBeVisible();
    await expect(
      canvas.getByRole('group', { name: i18n.t('language.label') }),
    ).toBeVisible();
    await expect(
      canvas.getByText(
        i18n.t('auth.userMenu.signedInAs', { name: 'Demo Owner' }),
      ),
    ).toBeVisible();
    await expect(
      canvas.getByRole('button', { name: i18n.t('auth.userMenu.signOut') }),
    ).toBeVisible();
    await expect(
      canvas.getByRole('button', {
        name: i18n.t('notifications.bellUnread', { unread: '2' }),
      }),
    ).toBeVisible();
  },
};

// Signed in: only the items the user may open (UX only). The second item
// needs a permission this user lacks.
export const FilteredNavigation: Story = {
  render: () =>
    renderLayout(signedIn, [
      { to: '/', labelKey: 'nav.home', end: true },
      { to: '/audit', labelKey: 'notFound.title', permission: 'audit.view' },
    ]),
  play: async ({ canvas }) => {
    const nav = canvas.getByRole('navigation', { name: i18n.t('nav.label') });
    const links = nav.querySelectorAll('a');
    await expect(links).toHaveLength(1);
    await expect(links[0]).toHaveTextContent(i18n.t('nav.home'));
  },
};

// Signed out (the login page): brand and switchers only.
export const SignedOut: Story = {
  render: () => renderLayout({ status: 'signedOut' }),
  play: async ({ canvas }) => {
    await expect(
      canvas.queryByRole('navigation', { name: i18n.t('nav.label') }),
    ).toBeNull();
    await expect(
      canvas.queryByRole('button', { name: i18n.t('auth.userMenu.signOut') }),
    ).toBeNull();
    await expect(
      canvas.queryByRole('button', { name: /^Notifica/ }),
    ).toBeNull();
    await expect(
      canvas.getByRole('group', { name: i18n.t('theme.label') }),
    ).toBeVisible();
  },
};

// Narrow (320 px): navigation and both switchers collapse behind the toggle.
export const Narrow: Story = {
  globals: { viewport: { value: 'narrow320' } },
  play: async ({ canvas, userEvent }) => {
    const toggle = canvas.getByRole('button', { name: i18n.t('nav.toggle') });
    await expect(toggle).toHaveAttribute('aria-expanded', 'false');
    await expect(
      canvas.queryByRole('navigation', { name: i18n.t('nav.label') }),
    ).toBeNull();

    await userEvent.click(toggle);
    await expect(toggle).toHaveAttribute('aria-expanded', 'true');
    await expect(
      canvas.getByRole('navigation', { name: i18n.t('nav.label') }),
    ).toBeVisible();
    await expect(
      canvas.getByRole('group', { name: i18n.t('theme.label') }),
    ).toBeVisible();
    await expect(
      canvas.getByRole('group', { name: i18n.t('language.label') }),
    ).toBeVisible();
    await expect(
      canvas.getByRole('button', { name: i18n.t('auth.userMenu.signOut') }),
    ).toBeVisible();
    await expect(
      canvas.getByRole('button', {
        name: i18n.t('notifications.bellUnread', { unread: '2' }),
      }),
    ).toBeVisible();
    // Nothing overflows horizontally at 320 px.
    await expect(document.documentElement.scrollWidth).toBeLessThanOrEqual(320);

    await userEvent.click(toggle);
    await expect(toggle).toHaveAttribute('aria-expanded', 'false');
  },
};

// #36 F5, lg and up: the alert below the switchers, with the request
// reference and "Try again".
export const PreferenceSaveFailed: Story = {
  render: renderSaveFailed,
  play: async ({ canvas }) => {
    const alert = canvas.getByRole('alert');
    await expect(alert).toBeVisible();
    await expect(alert).toHaveTextContent(i18n.t('preferences.saveFailed'));
    await expect(alert).toHaveTextContent('req-preferences-0001');
    await expect(
      canvas.getByRole('button', { name: i18n.t('feedback.retry') }),
    ).toBeVisible();
  },
};

// #36 F5 at 320 px: the alert sits in the collapsed header menu, below the
// switchers, and nothing overflows.
export const PreferenceSaveFailedNarrow: Story = {
  render: renderSaveFailed,
  globals: { viewport: { value: 'narrow320' } },
  play: async ({ canvas, userEvent }) => {
    await expect(canvas.queryByRole('alert')).toBeNull();
    await userEvent.click(
      canvas.getByRole('button', { name: i18n.t('nav.toggle') }),
    );
    const alert = canvas.getByRole('alert');
    await expect(alert).toBeVisible();
    await expect(alert).toHaveTextContent(i18n.t('preferences.saveFailed'));
    await expect(
      canvas.getByRole('button', { name: i18n.t('feedback.retry') }),
    ).toBeVisible();
    await expect(document.documentElement.scrollWidth).toBeLessThanOrEqual(320);
  },
};
