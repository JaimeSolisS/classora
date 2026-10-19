import { createContext, useContext } from 'react';
import { canAccess } from './access';
import type { LoginNoticeState } from './loginNotice';
import type { PermissionCode, Session, SessionUserBody } from './session';

// The auth context, filled by AuthProvider (and by fixed values in stories).

export interface AuthContextValue {
  /** The session query, as a state (#28 A1). */
  session: Session;
  /** Refetches /auth/me (the guard's "Try again"). */
  refetchSession: () => void;
  /**
   * Takes the session of a successful login: clears every cached response of
   * an earlier session, keeps the CSRF token and sets the session query data
   * (no extra /auth/me call).
   */
  signIn: (body: SessionUserBody) => void;
  /**
   * POST /auth/logout, then clears the token, the caches and the UI store and
   * goes to /login. Rejects (and stays signed in) when the server session may
   * still be live (#28 E3).
   */
  signOut: () => Promise<void>;
  /**
   * The server already ended this session (the user changed their own roles,
   * #170 G4): the local steps of signOut without calling /auth/logout (clear
   * the token, every cached query and mutation and the UI store), then
   * /login with `notice` in the router state.
   */
  signOutLocally: (notice: LoginNoticeState) => void;
}

export const AuthContext = createContext<AuthContextValue | null>(null);

export function useAuth(): AuthContextValue {
  const value = useContext(AuthContext);
  if (value === null) {
    throw new Error('useAuth must be used inside AuthProvider');
  }
  return value;
}

/** The session state: loading, signedIn, signedOut or error (#28 A1). */
export function useSession(): Session {
  return useAuth().session;
}

/**
 * Whether the signed-in user holds `permission`, to hide a button or a link.
 * UX only: the API checks every request (spec §16).
 */
export function useCan(permission: PermissionCode): boolean {
  const session = useSession();
  return (
    session.status === 'signedIn' && canAccess(session.user, { permission })
  );
}
