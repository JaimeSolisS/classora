import { useEffect, useRef, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { useNavigate } from 'react-router';
import { api, ApiError, apiCall, NO_AUTH_HANDLING } from '@/app/api';
import { useDocumentTitle } from '@/app/useDocumentTitle';
import { loginNoticeState } from '../loginNotice';
import { useFragmentToken } from '../useFragmentToken';
import { useAuth } from '../useSession';
import { InviteView, type InviteViewState } from './InviteView';

// /invite (#29 J): a public page next to /login, outside the guard. It reads
// the one-time token from the URL fragment and removes the fragment at once
// (useFragmentToken). Signed in, it makes no API call and shows the
// signed-in notice. Signed out, it looks the link up and, when valid, asks
// for a new password; after redemption it sends the person to /login with
// a notice and their username (router state, never the URL).
//
// The token and the password are never retained (#28 B8): no query, no
// mutation, no store, no browser storage; the token lives in this page's
// state only, the password in the form's.

interface Invitee {
  username: string;
  displayName: string;
}

type LookupState =
  | { status: 'checking' }
  | { status: 'found'; invitee: Invitee }
  | { status: 'invalid' }
  | { status: 'rateLimited' }
  | { status: 'error'; error: unknown };

function lookupFailure(error: unknown): LookupState {
  if (error instanceof ApiError && error.code === 'token_invalid') {
    return { status: 'invalid' };
  }
  if (error instanceof ApiError && error.code === 'rate_limited') {
    return { status: 'rateLimited' };
  }
  return { status: 'error', error };
}

export function InvitePage() {
  const { t } = useTranslation();
  useDocumentTitle(t('invite.title'));
  const token = useFragmentToken();
  const { session, signOut, refetchSession } = useAuth();
  const navigate = useNavigate();
  const [lookup, setLookup] = useState<LookupState>({ status: 'checking' });
  // Changed by "Try again" to run the lookup once more.
  const [attempt, setAttempt] = useState(0);
  const sessionStatus = session.status;
  // Set once the page saw a signed-in user: signing out from the notice never
  // turns into a lookup (the person must open the link again, J3).
  const sawSignedIn = useRef(false);

  useEffect(() => {
    if (sessionStatus === 'signedIn') {
      sawSignedIn.current = true;
    }
    if (
      token === null ||
      sessionStatus !== 'signedOut' ||
      sawSignedIn.current
    ) {
      return;
    }
    let active = true;
    apiCall(
      () => api.POST('/api/v1/auth/invitation/lookup', { body: { token } }),
      NO_AUTH_HANDLING,
    ).then(
      (found) => {
        if (active) {
          setLookup({
            status: 'found',
            invitee: {
              username: found.username,
              displayName: found.display_name,
            },
          });
        }
      },
      (error: unknown) => {
        if (active) {
          setLookup(lookupFailure(error));
        }
      },
    );
    return () => {
      active = false;
    };
  }, [token, sessionStatus, attempt]);

  async function redeem(invitee: Invitee, password: string) {
    if (token === null) {
      return;
    }
    try {
      await apiCall(
        () =>
          api.POST('/api/v1/auth/invitation/redeem', {
            body: { token, password },
          }),
        NO_AUTH_HANDLING,
      );
    } catch (error) {
      if (error instanceof ApiError && error.code === 'token_invalid') {
        setLookup({ status: 'invalid' });
        return;
      }
      throw error;
    }
    void navigate('/login', {
      replace: true,
      state: loginNoticeState('invitation_redeemed', invitee.username),
    });
  }

  function retry() {
    setLookup({ status: 'checking' });
    setAttempt((value) => value + 1);
  }

  return <InviteView state={viewState()} />;

  function viewState(): InviteViewState {
    if (session.status === 'loading') {
      return { kind: 'sessionLoading' };
    }
    if (session.status === 'error') {
      return {
        kind: 'sessionError',
        error: session.error,
        onRetry: refetchSession,
      };
    }
    if (session.status === 'signedIn') {
      return {
        kind: 'signedIn',
        name: session.user.display_name,
        onSignOut: signOut,
      };
    }
    if (token === null) {
      return { kind: 'incomplete' };
    }
    switch (lookup.status) {
      case 'checking':
        return { kind: 'checking' };
      case 'invalid':
        return { kind: 'invalid' };
      case 'rateLimited':
        return { kind: 'rateLimited' };
      case 'error':
        return { kind: 'error', error: lookup.error, onRetry: retry };
      case 'found': {
        const { invitee } = lookup;
        return {
          kind: 'form',
          username: invitee.username,
          displayName: invitee.displayName,
          onSubmit: (password) => redeem(invitee, password),
        };
      }
    }
  }
}
