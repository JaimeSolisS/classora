import { Menu, PanelLeftClose, PanelLeftOpen, X } from 'lucide-react';
import {
  useEffect,
  useRef,
  useSyncExternalStore,
  type MouseEvent,
} from 'react';
import { useTranslation } from 'react-i18next';
import { Link, NavLink, Outlet, useLocation, useMatch } from 'react-router';
import { Button } from '@/components/ui/button';
import {
  Tooltip,
  TooltipContent,
  TooltipProvider,
  TooltipTrigger,
} from '@/components/ui/tooltip';
import { canAccess } from '@/features/auth/access';
import { UserMenu } from '@/features/auth/UserMenu';
import { PRIVACY_NOTICE_PATH } from '@/features/auth/session';
import { useSession } from '@/features/auth/useSession';
import { NotificationBell } from '@/features/notifications/NotificationBell';
import { PreferenceSaveAlert } from '@/features/preferences/PreferenceSaveAlert';
import { cn } from '@/lib/utils';
import { useUiStore } from '@/stores/ui';
import { NAV_SECTIONS, type NavItem, NAV_ITEMS } from '../navigation';
import { APP_NAME } from '../useDocumentTitle';
import { HeaderLanguageSwitcher, HeaderThemeSwitcher } from './HeaderSwitchers';

export const MAIN_ID = 'main';
const HEADER_MENU_ID = 'app-header-menu';
const NAV_ID = 'app-nav';

const focusRing =
  'rounded-md outline-none focus-visible:ring-3 focus-visible:ring-ring';

// The `lg` breakpoint (1024 px). Without matchMedia (jsdom) the shell
// behaves as below `lg`: the drawer contract, no rail tooltips.
const LG_QUERY = '(min-width: 1024px)';

function lgQuery(): MediaQueryList | null {
  return typeof window.matchMedia === 'function'
    ? window.matchMedia(LG_QUERY)
    : null;
}

function subscribeToLg(onChange: () => void): () => void {
  const query = lgQuery();
  if (!query) {
    return () => {};
  }
  query.addEventListener('change', onChange);
  return () => query.removeEventListener('change', onChange);
}

function isLg(): boolean {
  return lgQuery()?.matches ?? false;
}

// Moves focus to <main> without changing the URL hash (the router owns it).
function skipToMain(event: MouseEvent<HTMLAnchorElement>) {
  const main = document.getElementById(MAIN_ID);
  if (main) {
    event.preventDefault();
    main.focus();
  }
}

/** The visible items in display order: Home (no section), then each section
 * that has a visible item (#546 B2). */
function groupBySection(items: readonly NavItem[]) {
  const groups: { section: NavItem['section']; items: NavItem[] }[] = [
    { section: undefined, items: items.filter((item) => !item.section) },
    ...NAV_SECTIONS.map((section) => ({
      section,
      items: items.filter((item) => item.section === section),
    })),
  ];
  return groups.filter((group) => group.items.length > 0);
}

interface AppLayoutProps {
  /** The navigation items (tests pass their own). */
  navItems?: readonly NavItem[];
}

// The shell around every page: skip link, header (the "Menu" toggle, brand,
// primary navigation, theme and language switchers, the signed-in user and
// "Sign out") and the main area. The items shown are the ones the user may
// open (UX only, #28 F4).
// #546: at `lg` and up the navigation is a fixed sidebar on the left, still
// inside <header> (E1), grouped in sections (B2); it collapses to an icon rail
// with tooltips, and the browser remembers the choice (`classora.sidebar`,
// stores/ui.ts). The switchers, the bell and the user menu stay in the top
// bar. Below `lg` the navigation and the utilities are a modal drawer behind
// "Menu" (E6): <main> is inert while it is open; Escape or the backdrop closes
// it and focuses "Menu"; following a link, or widening to `lg`, closes it.
// One <nav> element serves both. Signed out (the login page), the
// privacy-notice page and every page while the notice is still to accept
// (#174 G1) have no navigation items: no sidebar, only the brand, the
// switchers and the user menu.
// The notification bell (#41 E1) sits next to the user menu, inside the
// drawer below `lg`, once the privacy notice is accepted. A switcher choice
// that couldn't be saved to the account shows an alert below the switchers
// (#36 F5).
export function AppLayout({ navItems = NAV_ITEMS }: AppLayoutProps) {
  const { t } = useTranslation();
  const navOpen = useUiStore((state) => state.navOpen);
  const toggleNav = useUiStore((state) => state.toggleNav);
  const setNavOpen = useUiStore((state) => state.setNavOpen);
  const collapsed = useUiStore((state) => state.sidebarCollapsed);
  const toggleSidebar = useUiStore((state) => state.toggleSidebar);
  const isDesktop = useSyncExternalStore(subscribeToLg, isLg, () => false);
  const location = useLocation();
  const session = useSession();
  const onNotice = useMatch(PRIVACY_NOTICE_PATH) !== null;
  const user = session.status === 'signedIn' ? session.user : null;
  const visibleItems =
    user && !user.privacy_notice_required && !onNotice
      ? navItems.filter((item) => canAccess(user, item))
      : [];
  const hasNav = visibleItems.length > 0;
  // The icon rail: collapsed, at `lg` and up only (B6).
  const rail = hasNav && collapsed && isDesktop;

  const toggleRef = useRef<HTMLButtonElement>(null);
  const menuRef = useRef<HTMLDivElement>(null);
  const wasOpen = useRef(navOpen);

  function closeDrawerToToggle() {
    setNavOpen(false);
    toggleRef.current?.focus();
  }

  // E4: following a link (a nav or user-menu link, or a notification opened
  // from the bell) closes the drawer.
  useEffect(() => {
    setNavOpen(false);
  }, [location.key, setNavOpen]);

  // E5: widening to `lg` closes the drawer, so <main> is no longer inert.
  useEffect(() => {
    if (isDesktop && navOpen) {
      setNavOpen(false);
    }
  }, [isDesktop, navOpen, setNavOpen]);

  // E2: on open, focus the first link in the drawer (or its first control
  // when there are no items). E4: on a close that left focus on a hidden
  // element of the drawer, focus <main> (never a hidden element).
  useEffect(() => {
    const menu = menuRef.current;
    if (navOpen && !wasOpen.current && menu) {
      const first =
        menu.querySelector<HTMLElement>('nav a[href]') ??
        menu.querySelector<HTMLElement>('a[href], button:not([disabled])');
      first?.focus();
    }
    if (!navOpen && wasOpen.current && !isDesktop) {
      const active = document.activeElement;
      if (!active || active === document.body || menu?.contains(active)) {
        document.getElementById(MAIN_ID)?.focus();
      }
    }
    wasOpen.current = navOpen;
  }, [navOpen, isDesktop]);

  // E3: Escape closes the drawer and focuses "Menu" (a popover or tooltip
  // that handled the Escape first keeps the drawer open).
  useEffect(() => {
    if (!navOpen) {
      return;
    }
    function onKeyDown(event: KeyboardEvent) {
      if (event.key === 'Escape' && !event.defaultPrevented) {
        setNavOpen(false);
        toggleRef.current?.focus();
      }
    }
    document.addEventListener('keydown', onKeyDown);
    return () => document.removeEventListener('keydown', onKeyDown);
  }, [navOpen, setNavOpen]);

  // E4: a link followed inside the drawer closes it at once (also a link to
  // the current page, which doesn't change the location).
  function onMenuClick(event: MouseEvent<HTMLDivElement>) {
    if (
      navOpen &&
      event.target instanceof Element &&
      event.target.closest('a[href]')
    ) {
      setNavOpen(false);
    }
  }

  function navLink(item: NavItem) {
    const Icon = item.icon;
    return (
      <li key={item.to}>
        <Tooltip>
          <TooltipTrigger asChild>
            <NavLink
              to={item.to}
              end={item.end}
              className={cn(
                'relative flex min-h-10 items-center gap-3 px-3 text-sm font-medium hover:bg-muted aria-[current=page]:bg-muted aria-[current=page]:font-semibold aria-[current=page]:before:absolute aria-[current=page]:before:inset-y-1.5 aria-[current=page]:before:left-0 aria-[current=page]:before:w-1 aria-[current=page]:before:rounded-full aria-[current=page]:before:bg-primary',
                collapsed && 'lg:justify-center lg:px-0',
                focusRing,
              )}
            >
              <Icon aria-hidden="true" className="size-5 shrink-0" />
              <span className={cn('min-w-0', collapsed && 'lg:sr-only')}>
                {t(item.labelKey)}
              </span>
            </NavLink>
          </TooltipTrigger>
          {rail ? (
            <TooltipContent side="right" sideOffset={8}>
              {t(item.labelKey)}
            </TooltipContent>
          ) : null}
        </Tooltip>
      </li>
    );
  }

  const collapseName = collapsed ? t('nav.expand') : t('nav.collapse');
  const CollapseIcon = collapsed ? PanelLeftOpen : PanelLeftClose;

  return (
    <TooltipProvider>
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
        <header
          className={cn(
            'min-w-0 border-b border-border transition-[margin] duration-200 motion-reduce:transition-none',
            hasNav && (collapsed ? 'lg:ml-16' : 'lg:ml-60'),
          )}
        >
          <div className="flex flex-wrap items-center gap-x-6 gap-y-3 px-4 py-3">
            <Button
              ref={toggleRef}
              type="button"
              variant="outline"
              size="icon"
              className={cn('lg:hidden', navOpen && 'relative z-50')}
              aria-expanded={navOpen}
              aria-controls={HEADER_MENU_ID}
              aria-label={t('nav.toggle')}
              onClick={toggleNav}
            >
              {navOpen ? <X aria-hidden="true" /> : <Menu aria-hidden="true" />}
            </Button>
            <Link
              to="/"
              inert={navOpen}
              className={cn('px-1 text-lg font-semibold', focusRing)}
            >
              {APP_NAME}
            </Link>
            {navOpen ? (
              // The drawer's backdrop (E6): pointer only; Escape is the
              // keyboard way out.
              <div
                aria-hidden="true"
                className="fixed inset-0 z-30 bg-background/80 lg:hidden"
                onClick={closeDrawerToToggle}
              />
            ) : null}
            {/* Clicks only bubble here, to close the drawer after a link. */}
            <div
              ref={menuRef}
              id={HEADER_MENU_ID}
              onClick={onMenuClick}
              className={cn(
                'flex-col gap-4',
                navOpen
                  ? 'fixed inset-y-0 left-0 z-40 flex w-[min(18rem,85vw)] overflow-y-auto overscroll-contain border-r border-border bg-background px-4 pt-16 pb-4 shadow-lg'
                  : 'hidden',
                'lg:static lg:z-auto lg:flex lg:w-auto lg:min-w-0 lg:flex-1 lg:flex-row lg:flex-wrap lg:items-center lg:overflow-visible lg:border-0 lg:bg-transparent lg:p-0 lg:shadow-none',
              )}
            >
              {hasNav ? (
                <nav
                  id={NAV_ID}
                  aria-label={t('nav.label')}
                  className={cn(
                    'flex flex-col gap-1 lg:fixed lg:inset-y-0 lg:left-0 lg:z-30 lg:overflow-x-hidden lg:overflow-y-auto lg:border-r lg:border-border lg:bg-background lg:p-2 lg:transition-[width] lg:duration-200 lg:motion-reduce:transition-none',
                    collapsed ? 'lg:w-16' : 'lg:w-60',
                  )}
                >
                  <Tooltip>
                    <TooltipTrigger asChild>
                      <Button
                        type="button"
                        variant="ghost"
                        size="icon"
                        className={cn(
                          'hidden size-10 lg:inline-flex',
                          collapsed ? 'lg:self-center' : 'lg:self-end',
                        )}
                        aria-controls={NAV_ID}
                        aria-label={collapseName}
                        onClick={toggleSidebar}
                      >
                        <CollapseIcon aria-hidden="true" />
                      </Button>
                    </TooltipTrigger>
                    {rail ? (
                      <TooltipContent side="right" sideOffset={8}>
                        {collapseName}
                      </TooltipContent>
                    ) : null}
                  </Tooltip>
                  {groupBySection(visibleItems).map(({ section, items }) =>
                    section ? (
                      <div
                        key={section}
                        className={cn(
                          'mt-3',
                          collapsed &&
                            'lg:mt-2 lg:border-t lg:border-border lg:pt-2',
                        )}
                      >
                        <p
                          id={`${NAV_ID}-${section}`}
                          className={cn(
                            'px-3 pb-1 text-xs font-semibold text-muted-foreground',
                            collapsed && 'lg:sr-only',
                          )}
                        >
                          {t(`nav.sections.${section}`)}
                        </p>
                        <ul
                          aria-labelledby={`${NAV_ID}-${section}`}
                          className="flex flex-col gap-1"
                        >
                          {items.map(navLink)}
                        </ul>
                      </div>
                    ) : (
                      <ul key="home" className="flex flex-col gap-1">
                        {items.map(navLink)}
                      </ul>
                    ),
                  )}
                </nav>
              ) : null}
              <div className="flex flex-wrap items-center gap-4 lg:ml-auto lg:justify-end">
                <HeaderThemeSwitcher />
                <HeaderLanguageSwitcher />
                <NotificationBell />
                <UserMenu />
              </div>
              {/* #36 F5: a preference that couldn't be saved to the account. */}
              <PreferenceSaveAlert className="lg:ml-auto lg:basis-full" />
            </div>
          </div>
        </header>
        <main
          id={MAIN_ID}
          tabIndex={-1}
          inert={navOpen}
          className={cn(
            'min-w-0 flex-1 p-4 outline-none transition-[margin] duration-200 motion-reduce:transition-none',
            hasNav && (collapsed ? 'lg:ml-16' : 'lg:ml-60'),
          )}
        >
          <Outlet />
        </main>
      </div>
    </TooltipProvider>
  );
}
