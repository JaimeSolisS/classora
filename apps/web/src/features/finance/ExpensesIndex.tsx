import { Navigate } from 'react-router';
import { canAccess } from '@/features/auth/access';
import { ForbiddenPage } from '@/features/auth/ForbiddenPage';
import { useSession } from '@/features/auth/useSession';
import { EXPENSES_SECTIONS } from './sections';

// `/expenses` (M7-11b A3): opens the first tab the user may open, the
// PayrollIndex pattern. The guard has already checked the route's access.

export function ExpensesIndex() {
  const session = useSession();
  const user = session.status === 'signedIn' ? session.user : null;
  const first = EXPENSES_SECTIONS.find((section) =>
    canAccess(user, section.requires),
  );
  return first ? <Navigate to={first.to} replace /> : <ForbiddenPage />;
}
