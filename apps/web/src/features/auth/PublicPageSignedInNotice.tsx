import { useState } from 'react';
import { useTranslation } from 'react-i18next';
import { ErrorAlert } from '@/components/feedback/ErrorAlert';
import { Spinner } from '@/components/feedback/Spinner';
import { Button } from '@/components/ui/button';

interface PublicPageSignedInNoticeProps {
  /** The page heading (the page's one h1). */
  title: string;
  /** Why the page can't be used while signed in (already translated). */
  message: string;
  /** Signs out (the #28 logout); rejects when the session may still be live. */
  onSignOut: () => Promise<void>;
}

// A public page that is meant for someone without a session (an invitation
// link #29, a reset link #30, self-registration #169) shows this instead of
// its form when a user is signed in, so a staff member never sets another
// person's password in their own browser. Presentational: the page passes the
// text and the sign-out action.
export function PublicPageSignedInNotice({
  title,
  message,
  onSignOut,
}: PublicPageSignedInNoticeProps) {
  const { t } = useTranslation();
  const [error, setError] = useState<unknown>(null);
  const [pending, setPending] = useState(false);

  async function signOut() {
    setPending(true);
    setError(null);
    try {
      await onSignOut();
    } catch (caught) {
      setError(caught);
      setPending(false);
    }
  }

  return (
    <div className="mx-auto flex w-full max-w-md flex-col gap-6 py-6">
      <h1 className="text-2xl font-semibold">{title}</h1>
      <p className="rounded-md border border-border p-3">{message}</p>
      {error ? <ErrorAlert error={error} /> : null}
      <Button
        type="button"
        variant="outline"
        className="self-start"
        disabled={pending}
        onClick={() => void signOut()}
      >
        {pending ? (
          <Spinner label={t('auth.userMenu.signingOut')} showLabel />
        ) : (
          t('auth.userMenu.signOut')
        )}
      </Button>
    </div>
  );
}
