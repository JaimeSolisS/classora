import { useEffect, useRef, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { useNavigate } from 'react-router';
import { api, ApiError, apiCall, NO_AUTH_HANDLING } from '@/app/api';
import { useDocumentTitle } from '@/app/useDocumentTitle';
import { loginNoticeState, type LoginNoticeKind } from '../loginNotice';
import { useFragmentToken } from '../useFragmentToken';
import { useAuth } from '../useSession';
import {
  InviteView,
  ResetView,
  type RedeemLinkViewState,
} from './RedeemLinkView';

// A one-time link page (#29 J /invite, #30 J /reset): a public page next to
// /login, outside the guard. It reads the token from the URL fragment and
// removes the fragment at once (useFragmentToken). Signed in, it makes no
// API call and shows the signed-in notice. Signed out, it looks the link up
// and, when valid, asks for a new password; after redemption it sends the
// person to /login with a notice and their username (router state, never
// the URL).
//
// The token and the password are never retained (#28 B8): no query, no
// mutation, no store, no browser storage; the token lives in this page's
// state only, the password in the form's.

interface LinkHolder {
  username: string;
  displayName: string;
}

interface Found {
  username: string;
  display_name: string;
}

/** The two calls of one link kind and where it sends the person after. */
interface RedeemLinkKind {
  lookup: (token: string) => Promise<Found>;
  redeem: (token: string, password: string) => Promise<unknown>;
  notice: LoginNoticeKind;
}

type LookupState =
  | { status: 'checking' }
  | { status: 'found'; holder: LinkHolder }
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

/** The page's state for one link kind (the view renders it). */
function useRedeemLink(kind: RedeemLinkKind): RedeemLinkViewState {
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
  const { lookup: lookupCall, redeem: redeemCall, notice } = kind;

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
    lookupCall(token).then(
      (found) => {
        if (active) {
          setLookup({
            status: 'found',
            holder: {
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
  }, [token, sessionStatus, attempt, lookupCall]);

  async function redeem(holder: LinkHolder, password: string) {
    if (token === null) {
      return;
    }
    try {
      await redeemCall(token, password);
    } catch (error) {
      if (error instanceof ApiError && error.code === 'token_invalid') {
        setLookup({ status: 'invalid' });
        return;
      }
      throw error;
    }
    void navigate('/login', {
      replace: true,
      state: loginNoticeState(notice, holder.username),
    });
  }

  function retry() {
    setLookup({ status: 'checking' });
    setAttempt((value) => value + 1);
  }

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
      const { holder } = lookup;
      return {
        kind: 'form',
        username: holder.username,
        displayName: holder.displayName,
        onSubmit: (password) => redeem(holder, password),
      };
    }
  }
}

// Module-level, so the lookup effect never re-runs because of a new function.
const INVITATION: RedeemLinkKind = {
  lookup: (token) =>
    apiCall(
      () => api.POST('/api/v1/auth/invitation/lookup', { body: { token } }),
      NO_AUTH_HANDLING,
    ),
  redeem: (token, password) =>
    apiCall(
      () =>
        api.POST('/api/v1/auth/invitation/redeem', {
          body: { token, password },
        }),
      NO_AUTH_HANDLING,
    ),
  notice: 'invitation_redeemed',
};

const PASSWORD_RESET: RedeemLinkKind = {
  lookup: (token) =>
    apiCall(
      () => api.POST('/api/v1/auth/password-reset/lookup', { body: { token } }),
      NO_AUTH_HANDLING,
    ),
  redeem: (token, password) =>
    apiCall(
      () =>
        api.POST('/api/v1/auth/password-reset/redeem', {
          body: { token, password },
        }),
      NO_AUTH_HANDLING,
    ),
  notice: 'password_reset',
};

/** /invite (#29 J): set the password of an invited account. */
export function InvitePage() {
  const { t } = useTranslation();
  useDocumentTitle(t('invite.title'));
  return <InviteView state={useRedeemLink(INVITATION)} />;
}

/** /reset (#30 J): set a new password with a staff-issued reset link. */
export function ResetPage() {
  const { t } = useTranslation();
  useDocumentTitle(t('reset.title'));
  return <ResetView state={useRedeemLink(PASSWORD_RESET)} />;
}
