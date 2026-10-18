import { Outlet } from 'react-router';

/**
 * Placeholder route guard wrapping the app pages.
 *
 * Guards are UX only: the server is authoritative and checks every request
 * (architecture §8, spec §16). M2-10 replaces this body with the session and
 * permission check and the redirect to login. Until then it renders its child
 * route unconditionally: no fake roles, no storage, no API call.
 */
export function RouteGuard() {
  return <Outlet />;
}
