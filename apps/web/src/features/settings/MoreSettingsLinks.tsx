import { useId } from 'react';
import { useTranslation } from 'react-i18next';
import { Link } from 'react-router';
import type { MoreSettingsLink } from './moreLinks';

// "More settings" (M8-04b D1, E1): links from the General tab to the central
// settings that live in their own areas (spec §9), each shown only when its
// route would open for the user (the same permission as the route's
// handle). With no link visible the section is not rendered.

export function MoreSettingsLinks({
  links,
}: {
  links: readonly MoreSettingsLink[];
}) {
  const { t } = useTranslation();
  const titleId = useId();
  if (links.length === 0) {
    return null;
  }
  return (
    <section
      aria-labelledby={titleId}
      className="flex w-full max-w-3xl flex-col gap-3 border-t border-border pt-6"
    >
      <h2 id={titleId} className="text-lg font-semibold">
        {t('settings.more.title')}
      </h2>
      <ul className="flex flex-col gap-3">
        {links.map((link, index) => (
          <li key={link.to} className="flex flex-col gap-0.5">
            <Link
              to={link.to}
              aria-describedby={`${titleId}-${index}`}
              className="self-start font-medium underline underline-offset-4 outline-none hover:text-foreground focus-visible:ring-3 focus-visible:ring-ring"
            >
              {t(link.labelKey)}
            </Link>
            <span
              id={`${titleId}-${index}`}
              className="text-sm text-muted-foreground"
            >
              {t(link.descriptionKey)}
            </span>
          </li>
        ))}
      </ul>
    </section>
  );
}
