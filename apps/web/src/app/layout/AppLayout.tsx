import { Menu } from 'lucide-react';
import type { MouseEvent } from 'react';
import { useTranslation } from 'react-i18next';
import { Link, NavLink, Outlet, useMatch } from 'react-router';
import { Button } from '@/components/ui/button';
import { canAccess } from '@/features/auth/access';
import { UserMenu } from '@/features/auth/UserMenu';
import { PRIVACY_NOTICE_PATH } from '@/features/auth/session';
import { useSession } from '@/features/auth/useSession';
import { cn } from '@/lib/utils';
import { useUiStore } from '@/stores/ui';
import { NAV_ITEMS, type NavItem } from '../navigation';
import { APP_NAME } from '../useDocumentTitle';
import { HeaderLanguageSwitcher, HeaderThemeSwitcher } from './HeaderSwitchers';

export const MAIN_ID = 'main';
const HEADER_MENU_ID = 'app-header-menu';

const focusRing =
  'rounded-md outline-none focus-visible:ring-3 focus-visible:ring-ring';

// Moves focus to <main> without changing the URL hash (the router owns it).
function skipToMain(event: MouseEvent<HTMLAnchorElement>) {
  const main = document.getElementById(MAIN_ID);
  if (main) {
    event.preventDefault();
    main.focus();
  }
}

interface AppLayoutProps {
  /** The navigation items (tests pass their own). */
  navItems?: readonly NavItem[];
}

// The shell around every page: skip link, header (brand, primary navigation,
// theme and language switchers, the signed-in user and "Sign out") and the
// main area. Below `lg` the navigation, the switchers and the user menu
// collapse behind a toggle; the open state lives in the UI store. Signed out
// (the login page), the header has no navigation and no user menu. The items
// shown are the ones the user may open (UX only, #28 F4). The privacy-notice
// page, and every page while the notice is still to accept, show no
// navigation items (#174 G1): only the brand, the switchers and the user menu.
export function AppLayout({ navItems = NAV_ITEMS }: AppLayoutProps) {
  const { t } = useTranslation();
  const navOpen = useUiStore((state) => state.navOpen);
  const toggleNav = useUiStore((state) => state.toggleNav);
  const session = useSession();
  const onNotice = useMatch(PRIVACY_NOTICE_PATH) !== null;
  const user = session.status === 'signedIn' ? session.user : null;
  const visibleItems =
    user && !user.privacy_notice_required && !onNotice
      ? navItems.filter((item) => canAccess(user, item))
      : [];

  return (
    <div className="flex min-h-svh flex-col">
      <a
        href={`#${MAIN_ID}`}
        onClick={skipToMain}
        className={cn(
          'sr-only bg-background px-3 py-2 text-sm font-medium focus:not-sr-only focus:absolute focus:top-2 focus:left-2 focus:z-50',
          focusRing,
        )}
      >
        {t('shell.skipToMain')}
      </a>
      <header className="border-b border-border">
        <div className="flex flex-wrap items-center gap-x-6 gap-y-3 px-4 py-3">
          <Link to="/" className={cn('px-1 text-lg font-semibold', focusRing)}>
            {APP_NAME}
          </Link>
          <Button
            type="button"
            variant="outline"
            size="icon"
            className="ml-auto lg:hidden"
            aria-expanded={navOpen}
            aria-controls={HEADER_MENU_ID}
            aria-label={t('nav.toggle')}
            onClick={toggleNav}
          >
            <Menu aria-hidden="true" />
          </Button>
          <div
            id={HEADER_MENU_ID}
            className={cn(
              'w-full flex-col gap-4 lg:flex lg:w-auto lg:flex-1 lg:flex-row lg:items-center',
              navOpen ? 'flex' : 'hidden',
            )}
          >
            {visibleItems.length > 0 ? (
              <nav aria-label={t('nav.label')}>
                <ul className="flex flex-col gap-1 lg:flex-row">
                  {visibleItems.map((item) => (
                    <li key={item.to}>
                      <NavLink
                        to={item.to}
                        end={item.end}
                        className={cn(
                          'inline-flex min-h-9 items-center px-2 text-sm font-medium hover:bg-muted aria-[current=page]:underline aria-[current=page]:underline-offset-4',
                          focusRing,
                        )}
                      >
                        {t(item.labelKey)}
                      </NavLink>
                    </li>
                  ))}
                </ul>
              </nav>
            ) : null}
            <div className="flex flex-wrap items-center gap-4 lg:ml-auto">
              <HeaderThemeSwitcher />
              <HeaderLanguageSwitcher />
              <UserMenu />
            </div>
          </div>
        </div>
      </header>
      <main id={MAIN_ID} tabIndex={-1} className="flex-1 p-4 outline-none">
        <Outlet />
      </main>
    </div>
  );
}
