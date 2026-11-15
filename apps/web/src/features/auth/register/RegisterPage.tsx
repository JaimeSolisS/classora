import { useState } from 'react';
import { useTranslation } from 'react-i18next';
import { api, apiCall, NO_AUTH_HANDLING } from '@/app/api';
import { useDocumentTitle } from '@/app/useDocumentTitle';
import { useAuth } from '../useSession';
import type { RegistrationInput } from './RegisterForm';
import { RegisterView, type RegisterViewState } from './RegisterView';

// /register (#169 F): a public page next to /login, outside the guard. A
// parent or guardian, or a student, requests an account; it stays pending
// until the center approves it. Signed in, the page makes no request and
// shows the signed-in notice.
//
// The password is never retained (#28 B8): a direct call, never a TanStack
// mutation or query, no store and no browser storage; it lives only in the
// form's state, which is gone once the request is sent.

/** POST /auth/registrations (202, no body to keep). */
function submitRegistration(input: RegistrationInput): Promise<unknown> {
  return apiCall(
    () => api.POST('/api/v1/auth/registrations', { body: input }),
    NO_AUTH_HANDLING,
  );
}

export function RegisterPage() {
  const { t } = useTranslation();
  useDocumentTitle(t('register.title'));
  const { session, signOut, refetchSession } = useAuth();
  // The username to sign in with, once the request is sent.
  const [sentAs, setSentAs] = useState<string | null>(null);

  async function submit(input: RegistrationInput) {
    await submitRegistration(input);
    // The stored form: the API trims and lower-cases it.
    setSentAs(input.username.trim().toLowerCase());
  }

  let state: RegisterViewState;
  if (session.status === 'loading') {
    state = { kind: 'sessionLoading' };
  } else if (session.status === 'error') {
    state = {
      kind: 'sessionError',
      error: session.error,
      onRetry: refetchSession,
    };
  } else if (session.status === 'signedIn') {
    state = {
      kind: 'signedIn',
      name: session.user.display_name,
      onSignOut: signOut,
    };
  } else if (sentAs !== null) {
    state = { kind: 'sent', username: sentAs };
  } else {
    state = { kind: 'form', onSubmit: submit };
  }
  return <RegisterView state={state} />;
}
