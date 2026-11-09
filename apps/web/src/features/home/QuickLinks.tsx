import { useTranslation } from 'react-i18next';
import { Link } from 'react-router';
import type { SessionUser } from '@/features/auth/session';
import { HomeCard, LINK_CLASS } from './Card';
import { quickLinks } from './rules';

// The fallback of Home (HF-06 D3, E7): for an account with no agenda and no
// pending source, the pages it may open (its visible navigation items
// except Home, in nav order, at most 8), or a text that asks the center
// for access. UX only, as the navigation.

export function QuickLinks({ user }: { user: SessionUser | null }) {
  const { t } = useTranslation();
  const items = quickLinks(user);
  return (
    <HomeCard
      title={t('home.fallback.title')}
      level={2}
      data-home-section="fallback"
    >
      {items.length === 0 ? (
        <p className="text-sm" data-home-no-access>
          {t('home.fallback.none')}
        </p>
      ) : (
        <ul className="flex flex-col gap-1">
          {items.map(({ to, labelKey, icon: Icon }) => (
            <li key={to}>
              <Link to={to} className={`${LINK_CLASS} gap-2`}>
                <Icon aria-hidden="true" className="size-4 shrink-0" />
                {t(labelKey)}
              </Link>
            </li>
          ))}
        </ul>
      )}
    </HomeCard>
  );
}
