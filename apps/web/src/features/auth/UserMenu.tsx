import { useState } from 'react';
import { useTranslation } from 'react-i18next';
import { Link } from 'react-router';
import { ErrorAlert } from '@/components/feedback/ErrorAlert';
import { Spinner } from '@/components/feedback/Spinner';
import { Button } from '@/components/ui/button';
import { useAuth } from './useSession';

// The signed-in user in the header (#28 E1–E3): "Signed in as …" (text, not a
// link), "Change password" (#30 K1) and "Sign out". A failed logout shows its error and keeps the user
// signed in, because the server session may still be live.
export function UserMenu() {
  const { t } = useTranslation();
  const { session, signOut } = useAuth();
  const [error, setError] = useState<unknown>(null);
  const [pending, setPending] = useState(false);

  if (session.status !== 'signedIn') {
    return null;
  }

  async function onSignOut() {
    setPending(true);
    setError(null);
    try {
      await signOut();
    } catch (caught) {
      setError(caught);
      setPending(false);
    }
  }

  return (
    <div className="flex flex-col gap-2">
      <div className="flex flex-wrap items-center gap-x-3 gap-y-2">
        <p className="text-sm">
          {t('auth.userMenu.signedInAs', { name: session.user.display_name })}
        </p>
        <Link
          to="/account/password"
          className="inline-flex min-h-8 items-center rounded-md px-1 text-sm font-medium underline-offset-4 outline-none hover:underline focus-visible:ring-3 focus-visible:ring-ring"
        >
          {t('account.password.link')}
        </Link>
        <Button
          type="button"
          variant="outline"
          size="sm"
          disabled={pending}
          onClick={() => void onSignOut()}
        >
          {pending ? (
            <Spinner label={t('auth.userMenu.signingOut')} showLabel />
          ) : (
            t('auth.userMenu.signOut')
          )}
        </Button>
      </div>
      {error ? <ErrorAlert error={error} /> : null}
    </div>
  );
}
