import { useId, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { useCenterTimeZone } from '@/features/auth/useCenterTimeZone';
import type { SessionUser } from '@/features/auth/session';
import { todayInZone } from '@/lib/time';
import { HomeContent } from './HomeContent';
import { homePlan, isEmptyPlan } from './rules';

// The staff dashboard's fixed "Today" panel (HF-06 D1, E1): under the
// dashboard header, above the range control and the KPI widgets, and not
// part of the customizable layout. It holds the same sections as the role
// home (h3 under its h2). A dashboard user with no agenda and no pending
// source gets no panel.

export function TodayPanel({ user, now }: { user: SessionUser; now?: number }) {
  const { t } = useTranslation();
  const id = useId();
  const timeZone = useCenterTimeZone();
  // The clock when the page opened (stories and tests pass theirs).
  const [clock] = useState(() => now ?? Date.now());
  if (isEmptyPlan(homePlan(user))) {
    return null;
  }
  return (
    <section
      aria-labelledby={id}
      className="flex min-w-0 flex-col gap-4"
      data-home-today
    >
      <h2 id={id} className="text-xl font-semibold">
        {t('home.today.title')}
      </h2>
      <HomeContent
        user={user}
        timeZone={timeZone}
        today={todayInZone(timeZone, new Date(clock))}
        now={clock}
        level={3}
      />
    </section>
  );
}
