import { useEffect, useState } from 'react';
import { useTranslation } from 'react-i18next';
import {
  Navigate,
  useLocation,
  useNavigate,
  useSearchParams,
} from 'react-router';
import { api, apiCall, NO_AUTH_HANDLING } from '@/app/api';
import { useDocumentTitle } from '@/app/useDocumentTitle';
import { LoginForm, type LoginValues } from './LoginForm';
import { LOGIN_NOTICE_KEYS, readLoginNotice } from './loginNotice';
import { safeReturnPath } from './returnPath';
import { SessionLoading } from './SessionLoading';
import { useAuth } from './useSession';

// /login (#28 B): a public page outside the guard. A signed-in visitor (also
// right after a successful login) goes to the validated `next` or "/". A
// public page can send the person here with a notice and their username in
// the router state (#29 J5; loginNotice.ts).
export function LoginPage() {
  const { t } = useTranslation();
  useDocumentTitle(t('auth.login.title'));
  const { session } = useAuth();
  const [searchParams] = useSearchParams();

  if (session.status === 'signedIn') {
    return <Navigate replace to={safeReturnPath(searchParams.get('next'))} />;
  }
  if (session.status === 'loading') {
    return <SessionLoading />;
  }
  return (
    <LoginScreen
      sessionExpired={
        session.status === 'signedOut' && session.reason === 'session_expired'
      }
    />
  );
}

function LoginScreen({ sessionExpired }: { sessionExpired: boolean }) {
  const { t } = useTranslation();
  const { signIn } = useAuth();
  const location = useLocation();
  const navigate = useNavigate();
  // Captured once: a later focus refetch (now authentication_required) does
  // not hide the notice; the next submit does (B7).
  const [expiredOnArrival] = useState(sessionExpired);
  // A notice from a public page (#29 J5), read once from the router state.
  const [pageNotice] = useState(() => readLoginNotice(location.state));
  const hasState = location.state !== null && location.state !== undefined;

  useEffect(() => {
    // Drop the state from the history entry, so a reload or Back does not
    // show the notice again. The path and query (`next`) stay.
    if (hasState) {
      void navigate(
        { pathname: location.pathname, search: location.search },
        { replace: true, state: null },
      );
    }
  }, [hasState, location.pathname, location.search, navigate]);

  async function login(values: LoginValues): Promise<void> {
    // A direct call, not a mutation: the password never enters the TanStack
    // mutation cache (B8). Every error reaches the form as is.
    const body = await apiCall(
      () =>
        api.POST('/api/v1/auth/login', {
          body: { username: values.username, password: values.password },
        }),
      NO_AUTH_HANDLING,
    );
    signIn(body);
  }

  return (
    <LoginForm
      onSubmit={login}
      sessionExpired={expiredOnArrival}
      notice={pageNotice ? t(LOGIN_NOTICE_KEYS[pageNotice.notice]) : undefined}
      initialUsername={pageNotice?.username}
    />
  );
}
