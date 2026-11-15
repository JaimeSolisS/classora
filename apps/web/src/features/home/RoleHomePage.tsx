import { useState } from 'react';
import { useTranslation } from 'react-i18next';
import { useDocumentTitle } from '@/app/useDocumentTitle';
import { useCenterTimeZone } from '@/features/auth/useCenterTimeZone';
import { useSession } from '@/features/auth/useSession';
import { todayInZone } from '@/lib/time';
import { HomeContent } from './HomeContent';
import { QuickLinks } from './QuickLinks';
import { homePlan, isEmptyPlan } from './rules';

// The role home (HF-06 D2, E5): Home for users without the staff dashboard
// (tutors, students, guardians and accounts with no role). A greeting, then
// the sections the roles allow, or the quick links when none applies. The
// document title stays the bare brand. "Today" is the center's date.

export function RoleHomePage({ now }: { now?: number } = {}) {
  const { t } = useTranslation();
  useDocumentTitle();
  const session = useSession();
  const timeZone = useCenterTimeZone();
  const user = session.status === 'signedIn' ? session.user : null;
  // The clock when the page opened (stories and tests pass theirs).
  const [clock] = useState(() => now ?? Date.now());
  const today = todayInZone(timeZone, new Date(clock));
  const empty = isEmptyPlan(homePlan(user));
  return (
    <div className="flex min-w-0 flex-col gap-6" data-home-page>
      <h1 className="text-2xl font-semibold">
        {t('home.greeting', { name: user?.display_name ?? '' })}
      </h1>
      {user === null ? null : empty ? (
        <QuickLinks user={user} />
      ) : (
        <HomeContent
          user={user}
          timeZone={timeZone}
          today={today}
          now={clock}
          level={2}
        />
      )}
    </div>
  );
}
