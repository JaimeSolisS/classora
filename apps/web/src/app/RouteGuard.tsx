import { Navigate, Outlet, useLocation, useMatches } from 'react-router';
import { ErrorAlert } from '@/components/feedback/ErrorAlert';
import { canAccess, routeRequirement } from '@/features/auth/access';
import { ForbiddenPage } from '@/features/auth/ForbiddenPage';
import { loginPath, privacyNoticePath } from '@/features/auth/session';
import { SessionLoading } from '@/features/auth/SessionLoading';
import { useAuth } from '@/features/auth/useSession';

/** The id of the /privacy-notice route (#174), the one page the guard shows
 * before the notice is accepted. */
export const PRIVACY_NOTICE_ROUTE_ID = 'privacy-notice';

/**
 * The route guard around every app page (#28 F2).
 *
 * Guards are UX only: the server is authoritative and checks every request
 * (architecture §8, spec §16). Every route under the guard needs a session;
 * a route may also require a permission with `handle: { permission }`.
 *
 * - loading: the session loading state (never the page, never a redirect);
 * - signed out: to /login?next=<this page> (just /login after "Sign out",
 *   with the session's login notice in the router state, #170 G4);
 * - error: the error with "Try again" (no redirect);
 * - signed in but the current privacy notice not accepted (#174 G1): to
 *   /privacy-notice?next=<this page> (replace), except on that page itself;
 * - a required permission the user lacks: ForbiddenPage at the same URL;
 * - otherwise the page.
 */
export function RouteGuard() {
  const { session, refetchSession } = useAuth();
  const location = useLocation();
  const matches = useMatches();

  switch (session.status) {
    case 'loading':
      return <SessionLoading />;
    case 'error':
      return <ErrorAlert error={session.error} onRetry={refetchSession} />;
    case 'signedOut':
      return (
        <Navigate
          replace
          to={session.reason === 'signed_out' ? '/login' : loginPath(location)}
          state={session.notice}
        />
      );
    case 'signedIn': {
      const onNotice = matches.some(
        (match) => match.id === PRIVACY_NOTICE_ROUTE_ID,
      );
      if (session.user.privacy_notice_required && !onNotice) {
        return <Navigate replace to={privacyNoticePath(location)} />;
      }
      const allowed = matches.every((match) =>
        canAccess(session.user, routeRequirement(match.handle)),
      );
      return allowed ? <Outlet /> : <ForbiddenPage />;
    }
  }
}
