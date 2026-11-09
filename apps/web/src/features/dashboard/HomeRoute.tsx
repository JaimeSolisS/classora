import { lazy, Suspense } from 'react';
import { LoadingState } from '@/components/feedback/LoadingState';
import { canAccess } from '@/features/auth/access';
import { useSession } from '@/features/auth/useSession';
import { RoleHomePage } from '@/features/home/RoleHomePage';
import { DASHBOARD_ACCESS } from './access';

// The index route `/` ("Inicio", M8-10 D1, #127 E4; HF-06 D1, D2): the
// dashboard (with its "Today" panel) for users who may see some KPI
// widget, else the role home (tutors, students, guardians and accounts
// with no role, who make no layout request). The dashboard is code-split,
// so neither its KPI sections (and Recharts) nor dnd-kit are in the entry
// chunk; the role home uses no FullCalendar, Recharts or dnd-kit.

const DashboardPage = lazy(async () => ({
  default: (await import('./DashboardPage')).DashboardPage,
}));

export function HomeRoute() {
  const session = useSession();
  const user = session.status === 'signedIn' ? session.user : null;
  if (!canAccess(user, DASHBOARD_ACCESS)) {
    return <RoleHomePage />;
  }
  return (
    <Suspense fallback={<LoadingState />}>
      <DashboardPage />
    </Suspense>
  );
}
