import { useContext } from 'react';
import { DEFAULT_CENTER_TIME_ZONE } from '@/lib/time';
import { AuthContext } from './useSession';

/**
 * The center's IANA time zone (#37): the business setting, as the session
 * carries it (`SessionUser.center_time_zone`, refreshed with /auth/me).
 * Signed out, or outside the auth context (tests, stories), it is the
 * default zone (ADR-0004).
 */
export function useCenterTimeZone(): string {
  const auth = useContext(AuthContext);
  return auth?.session.status === 'signedIn'
    ? auth.session.user.center_time_zone
    : DEFAULT_CENTER_TIME_ZONE;
}
