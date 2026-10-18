import { Menu } from 'lucide-react';
import type { MouseEvent } from 'react';
import { useTranslation } from 'react-i18next';
import { Link, NavLink, Outlet } from 'react-router';
import { Button } from '@/components/ui/button';
import { cn } from '@/lib/utils';
import { useUiStore } from '@/stores/ui';
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

// The shell around every page: skip link, header (brand, primary navigation,
// theme and language switchers) and the main area. Below `lg` the navigation
// and both switchers collapse behind a toggle; the open state lives in the
// UI store.
export function AppLayout() {
  const { t } = useTranslation();
  const navOpen = useUiStore((state) => state.navOpen);
  const toggleNav = useUiStore((state) => state.toggleNav);

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
            <nav aria-label={t('nav.label')}>
              <ul className="flex flex-col gap-1 lg:flex-row">
                <li>
                  <NavLink
                    to="/"
                    end
                    className={cn(
                      'inline-flex min-h-9 items-center px-2 text-sm font-medium hover:bg-muted aria-[current=page]:underline aria-[current=page]:underline-offset-4',
                      focusRing,
                    )}
                  >
                    {t('nav.home')}
                  </NavLink>
                </li>
              </ul>
            </nav>
            <div className="flex flex-wrap gap-4 lg:ml-auto">
              <HeaderThemeSwitcher />
              <HeaderLanguageSwitcher />
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
