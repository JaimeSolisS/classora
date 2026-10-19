import {
  useQuery,
  useQueryClient,
  type QueryClient,
} from '@tanstack/react-query';
import { useEffect, useLayoutEffect, useState, type ReactNode } from 'react';
import { useNavigate, type NavigateFunction } from 'react-router';
import {
  api,
  apiCall,
  isSessionEnd,
  setAuthHandler,
  type AuthHandler,
} from '@/app/api';
import { resetUiStore } from '@/stores/ui';
import { clearCsrfToken } from './csrf';
import type { LoginNoticeState } from './loginNotice';
import { ReauthDialog } from './ReauthDialog';
import {
  acceptSessionUser,
  fetchSession,
  SESSION_QUERY_KEY,
  SESSION_STALE_TIME_MS,
  type Session,
  type SessionData,
  type SessionUserBody,
  type SignedOutReason,
} from './session';
import { AuthContext, type AuthContextValue } from './useSession';

// Owns the session (#28): the ['auth', 'me'] query, the reactions to a
// session that ends, a CSRF failure or a reauthentication request from any
// API call (the AuthHandler of src/app/api.ts), login, logout and the
// password dialog. Rendered by the root route, inside the router.

interface ControllerDeps {
  queryClient: QueryClient;
  navigate: NavigateFunction;
  /** Re-renders the provider after the cache was cleared (see replaceSession). */
  rerender: () => void;
  setReauthOpen: (open: boolean) => void;
}

function sessionQueryOptions() {
  return {
    queryKey: SESSION_QUERY_KEY,
    queryFn: ({ signal }: { signal: AbortSignal }) => fetchSession(signal),
  };
}

/**
 * The provider's state that must not wait for a render: which user the app
 * shows, whether a session end is already being handled, and the pending
 * password dialog. Created once per provider.
 */
function createAuthController({
  queryClient,
  navigate,
  rerender,
  setReauthOpen,
}: ControllerDeps) {
  /** The id of the signed-in user the app shows, or null. */
  let signedInId: string | null = null;
  /** Set once a session end is handled, so it happens once (C1). */
  let ending = false;
  let reauth: {
    promise: Promise<boolean>;
    resolve: (ok: boolean) => void;
  } | null = null;
  /** The /auth/me refetch after `privacy_notice_required`, while in flight. */
  let noticeRefetch: Promise<unknown> | null = null;

  /**
   * Clears every cached query and mutation (`queryClient.clear()`), then
   * stores the new session. Nothing from the earlier session survives. The
   * provider re-renders so its session observer moves to the new query.
   */
  function replaceSession(data: SessionData): void {
    queryClient.clear();
    queryClient.setQueryData<SessionData>(SESSION_QUERY_KEY, data);
    signedInId = data.user?.id ?? null;
    rerender();
  }

  function finishReauth(ok: boolean): void {
    const pending = reauth;
    reauth = null;
    setReauthOpen(false);
    pending?.resolve(ok);
  }

  /** C1: the server ended the session. The guard then goes to /login. */
  function endSession(reason?: SignedOutReason): void {
    if (ending) {
      return;
    }
    ending = true;
    clearCsrfToken();
    finishReauth(false);
    replaceSession(reason ? { user: null, reason } : { user: null });
  }

  /** A5: /auth/me returned another user (for example another tab signed in). */
  function userChanged(data: SessionData): void {
    finishReauth(false);
    ending = false;
    replaceSession(data);
    void navigate('/', { replace: true });
  }

  /**
   * Clears the token, every cached query and mutation and the UI store, then
   * goes to /login (with `notice` in the router state). Later 401s from
   * requests still in flight are ignored (`ending`).
   */
  function signOutLocally(notice?: LoginNoticeState): void {
    ending = true;
    clearCsrfToken();
    finishReauth(false);
    // The guard also redirects to /login, with the same notice.
    replaceSession(
      notice
        ? { user: null, reason: 'signed_out', notice }
        : { user: null, reason: 'signed_out' },
    );
    resetUiStore();
    void navigate(
      '/login',
      notice ? { replace: true, state: notice } : { replace: true },
    );
  }

  const handler: AuthHandler = {
    sessionEnded(error) {
      endSession(
        error.code === 'session_expired' ? 'session_expired' : undefined,
      );
    },
    async refreshAfterCsrfFailure() {
      const before = signedInId;
      let data: SessionData;
      try {
        data = await queryClient.fetchQuery({
          ...sessionQueryOptions(),
          staleTime: 0,
          retry: false,
        });
      } catch {
        return false;
      }
      if (data.user === null) {
        endSession(data.reason);
        return false;
      }
      if (before === null || data.user.id !== before) {
        // Never send an action started as one user as another.
        userChanged(data);
        return false;
      }
      return true;
    },
    reauthenticate() {
      if (signedInId === null) {
        return Promise.resolve(false);
      }
      if (reauth === null) {
        let resolve: (ok: boolean) => void = () => {};
        const promise = new Promise<boolean>((done) => {
          resolve = done;
        });
        reauth = { promise, resolve };
        setReauthOpen(true);
      }
      // D5: every request waits for the same dialog.
      return reauth.promise;
    },
    privacyNoticeRequired() {
      // #174 G3: one refetch, whatever the number of failing requests. When
      // the cached session already asks for the notice, the guard is on it.
      const cached = queryClient.getQueryData<SessionData>(SESSION_QUERY_KEY);
      if (signedInId === null || cached?.user?.privacy_notice_required) {
        return;
      }
      if (noticeRefetch === null) {
        noticeRefetch = queryClient
          .fetchQuery({ ...sessionQueryOptions(), staleTime: 0, retry: false })
          .catch(() => undefined)
          .finally(() => {
            noticeRefetch = null;
          });
      }
    },
  };

  return {
    handler,

    /** Reacts to every new value of the session query (bootstrap, focus). */
    observe: (data: SessionData): void => {
      if (data.user === null) {
        if (signedInId !== null) {
          // A refetch found the session gone: same as C1.
          endSession(data.reason);
        }
        return;
      }
      if (signedInId !== null && signedInId !== data.user.id) {
        userChanged(data);
        return;
      }
      signedInId = data.user.id;
      ending = false;
    },

    signIn: (body: SessionUserBody): void => {
      const user = acceptSessionUser(body);
      ending = false;
      finishReauth(false);
      replaceSession({ user });
    },

    signOut: async (): Promise<void> => {
      try {
        // A 401 here means the session was already gone: also signed out.
        await apiCall(() => api.POST('/api/v1/auth/logout'), {
          endSessionOn401: false,
        });
      } catch (error) {
        if (!isSessionEnd(error)) {
          // E3: the server session may still be live; stay signed in.
          throw error;
        }
      }
      signOutLocally();
    },

    /** The local steps of signOut (#28 E2), then /login with `notice`. */
    signOutLocally,

    confirmReauth: async (password: string): Promise<void> => {
      // A direct call: the password never enters the mutation cache. A 401
      // ends the session (C1, which also closes the dialog); csrf_failed is
      // retried once (C3).
      await apiCall(
        () => api.POST('/api/v1/auth/reauthenticate', { body: { password } }),
        { reauthenticate: false },
      );
      finishReauth(true);
    },

    cancelReauth: (): void => {
      finishReauth(false);
    },
  };
}

function toSession(query: {
  data: SessionData | undefined;
  error: unknown;
  isError: boolean;
}): Session {
  if (query.data) {
    const data = query.data;
    if (data.user) {
      return { status: 'signedIn', user: data.user };
    }
    return {
      status: 'signedOut',
      ...(data.reason ? { reason: data.reason } : {}),
      ...(data.notice ? { notice: data.notice } : {}),
    };
  }
  if (query.isError) {
    return { status: 'error', error: query.error };
  }
  return { status: 'loading' };
}

export function AuthProvider({ children }: { children: ReactNode }) {
  const queryClient = useQueryClient();
  const navigate = useNavigate();
  const [, setEpoch] = useState(0);
  const [reauthOpen, setReauthOpen] = useState(false);
  const [controller] = useState(() =>
    createAuthController({
      queryClient,
      navigate,
      rerender: () => setEpoch((epoch) => epoch + 1),
      setReauthOpen,
    }),
  );

  // A2: no refetchInterval (no polling); refetch on focus once stale.
  const query = useQuery({
    ...sessionQueryOptions(),
    staleTime: SESSION_STALE_TIME_MS,
  });

  // Registered before any page effect can send a request.
  useLayoutEffect(() => {
    setAuthHandler(controller.handler);
    return () => setAuthHandler(null);
  }, [controller]);

  useEffect(() => {
    if (query.data) {
      controller.observe(query.data);
    }
  }, [controller, query.data]);

  const { refetch } = query;
  const value: AuthContextValue = {
    session: toSession(query),
    refetchSession: () => void refetch(),
    signIn: controller.signIn,
    signOut: controller.signOut,
    signOutLocally: controller.signOutLocally,
  };

  return (
    <AuthContext value={value}>
      {children}
      <ReauthDialog
        open={reauthOpen}
        onConfirm={controller.confirmReauth}
        onCancel={controller.cancelReauth}
      />
    </AuthContext>
  );
}
