import { lazy, Suspense } from 'react';
import { LoadingState } from '@/components/feedback/LoadingState';
import { canAccess } from '@/features/auth/access';
import { useSession } from '@/features/auth/useSession';
import { HomePage } from '@/pages/HomePage';
import { DASHBOARD_ACCESS } from './access';

// The index route `/` ("Inicio", M8-10 D1, #127 E4): the dashboard for
// users who may see some KPI widget, else the old home page (tutors,
// students and guardians, who make no layout request). The dashboard is
// code-split, so neither its KPI sections (and Recharts) nor dnd-kit are in
// the entry chunk.

const DashboardPage = lazy(async () => ({
  default: (await import('./DashboardPage')).DashboardPage,
}));

export function HomeRoute() {
  const session = useSession();
  const user = session.status === 'signedIn' ? session.user : null;
  if (!canAccess(user, DASHBOARD_ACCESS)) {
    return <HomePage />;
  }
  return (
    <Suspense fallback={<LoadingState />}>
      <DashboardPage />
    </Suspense>
  );
}
