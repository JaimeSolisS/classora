import { Navigate } from 'react-router';
import { canAccess } from '@/features/auth/access';
import { useSession } from '@/features/auth/useSession';
import { PAYROLL_ACCESS } from './access';

// `/payroll` (#425 A3, #110b A3): Finance and Owners keep the tutor list; a
// delegated Administrator, who can't open it, goes to the periods. The guard
// has already checked PAYROLL_ANY_ACCESS, so the session is signed in.

export function PayrollIndex() {
  const session = useSession();
  const user = session.status === 'signedIn' ? session.user : null;
  return (
    <Navigate
      to={
        canAccess(user, PAYROLL_ACCESS) ? '/payroll/tutors' : '/payroll/periods'
      }
      replace
    />
  );
}
