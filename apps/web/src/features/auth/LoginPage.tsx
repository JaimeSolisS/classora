import { useState } from 'react';
import { useTranslation } from 'react-i18next';
import { Navigate, useSearchParams } from 'react-router';
import { api, apiCall, NO_AUTH_HANDLING } from '@/app/api';
import { useDocumentTitle } from '@/app/useDocumentTitle';
import { LoginForm, type LoginValues } from './LoginForm';
import { safeReturnPath } from './returnPath';
import { SessionLoading } from './SessionLoading';
import { useAuth } from './useSession';

// /login (#28 B): the only page outside the guard. A signed-in visitor (also
// right after a successful login) goes to the validated `next` or "/".
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
  const { signIn } = useAuth();
  // Captured once: a later focus refetch (now authentication_required) does
  // not hide the notice; the next submit does (B7).
  const [expiredOnArrival] = useState(sessionExpired);

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

  return <LoginForm onSubmit={login} sessionExpired={expiredOnArrival} />;
}
