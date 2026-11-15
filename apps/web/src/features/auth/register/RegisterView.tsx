import { useTranslation } from 'react-i18next';
import { Link } from 'react-router';
import { ErrorAlert } from '@/components/feedback/ErrorAlert';
import { PublicPageSignedInNotice } from '../PublicPageSignedInNotice';
import { SessionLoading } from '../SessionLoading';
import { TEXT_LINK_CLASS } from '../textLink';
import { RegisterForm, type RegistrationInput } from './RegisterForm';

// /register in each of its states (#169 F), without any API call:
// RegisterPage decides the state, stories pass one directly.

export type RegisterViewState =
  | { kind: 'sessionLoading' }
  | { kind: 'sessionError'; error: unknown; onRetry: () => void }
  | { kind: 'signedIn'; name: string; onSignOut: () => Promise<void> }
  | { kind: 'form'; onSubmit: (input: RegistrationInput) => Promise<void> }
  | { kind: 'sent'; username: string };

export function RegisterView({ state }: { state: RegisterViewState }) {
  const { t } = useTranslation();

  if (state.kind === 'sessionLoading') {
    return <SessionLoading />;
  }
  if (state.kind === 'signedIn') {
    return (
      <PublicPageSignedInNotice
        title={t('register.title')}
        message={t('register.signedIn', { name: state.name })}
        onSignOut={state.onSignOut}
        homeLabel={t('register.goHome')}
      />
    );
  }

  return (
    <div className="mx-auto flex w-full max-w-sm flex-col gap-6 py-6">
      <h1 className="text-2xl font-semibold">{t('register.title')}</h1>
      {state.kind === 'form' ? <p>{t('register.intro')}</p> : null}
      {/* Rendered in the form and sent states, so the confirmation is
          announced when it replaces the form. */}
      <div role="status">
        {state.kind === 'sent' ? (
          <p className="rounded-md border border-border p-3">
            {t('register.sent', { username: state.username })}
          </p>
        ) : null}
      </div>
      {state.kind === 'sessionError' ? (
        <ErrorAlert error={state.error} onRetry={state.onRetry} />
      ) : null}
      {state.kind === 'form' ? (
        <RegisterForm onSubmit={state.onSubmit} />
      ) : null}
      <Link to="/login" className={TEXT_LINK_CLASS}>
        {t('register.backToLogin')}
      </Link>
    </div>
  );
}
