import { useTranslation } from 'react-i18next';
import { api, apiCall } from '@/app/api';
import { useDocumentTitle } from '@/app/useDocumentTitle';
import { useAuth } from '@/features/auth/useSession';
import {
  ChangePasswordForm,
  type PasswordChangeInput,
} from './ChangePasswordForm';

// /account/password (#30 K): any signed-in user changes their own password
// with the current one. The server ends every other session and rotates this
// one: the response is the new session's SessionUser with its new CSRF token,
// which the app takes through the same path as a login (signIn: the token is
// kept in memory, the ['auth', 'me'] cache is set without it). The user stays
// on the page, signed in.
export function ChangePasswordPage() {
  const { t } = useTranslation();
  useDocumentTitle(t('account.password.title'));
  const { session, signIn } = useAuth();
  const username = session.status === 'signedIn' ? session.user.username : '';

  async function change(input: PasswordChangeInput) {
    // A direct call, not a mutation: the passwords never enter the TanStack
    // mutation cache (#28 B8).
    const body = await apiCall(() =>
      api.POST('/api/v1/auth/password', {
        body: {
          current_password: input.currentPassword,
          new_password: input.newPassword,
        },
      }),
    );
    signIn(body);
  }

  return (
    <div className="flex flex-col gap-6">
      <h1 className="text-2xl font-semibold">{t('account.password.title')}</h1>
      <ChangePasswordForm username={username} onSubmit={change} />
    </div>
  );
}
