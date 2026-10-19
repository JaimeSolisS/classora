import { useTranslation } from 'react-i18next';
import { ErrorAlert } from '@/components/feedback/ErrorAlert';
import { LoadingState } from '@/components/feedback/LoadingState';
import { PublicPageSignedInNotice } from '../PublicPageSignedInNotice';
import { SessionLoading } from '../SessionLoading';
import { RedeemForm } from './RedeemForm';

// The /invite page in each of its states (#29 J), without any API call:
// InvitePage decides the state, stories pass one directly.

export type InviteViewState =
  | { kind: 'sessionLoading' }
  | { kind: 'sessionError'; error: unknown; onRetry: () => void }
  | { kind: 'incomplete' }
  | { kind: 'signedIn'; name: string; onSignOut: () => Promise<void> }
  | { kind: 'checking' }
  | { kind: 'invalid' }
  | { kind: 'rateLimited' }
  | { kind: 'error'; error: unknown; onRetry: () => void }
  | {
      kind: 'form';
      username: string;
      displayName: string;
      onSubmit: (password: string) => Promise<void>;
    };

export function InviteView({ state }: { state: InviteViewState }) {
  const { t } = useTranslation();
  const { t: tErrors } = useTranslation('errors');

  if (state.kind === 'sessionLoading') {
    return <SessionLoading />;
  }
  if (state.kind === 'signedIn') {
    return (
      <PublicPageSignedInNotice
        title={t('invite.title')}
        message={t('invite.signedIn', { name: state.name })}
        onSignOut={state.onSignOut}
      />
    );
  }

  let content;
  switch (state.kind) {
    case 'sessionError':
    case 'error':
      content = <ErrorAlert error={state.error} onRetry={state.onRetry} />;
      break;
    case 'incomplete':
      content = <Notice>{t('invite.incomplete')}</Notice>;
      break;
    case 'checking':
      content = <LoadingState rows={2} label={t('invite.checking')} />;
      break;
    case 'invalid':
      content = <Notice>{tErrors('token_invalid')}</Notice>;
      break;
    case 'rateLimited':
      content = <Notice>{tErrors('rate_limited')}</Notice>;
      break;
    case 'form':
      content = (
        <RedeemForm
          username={state.username}
          displayName={state.displayName}
          onSubmit={state.onSubmit}
        />
      );
      break;
  }

  return (
    <div className="mx-auto flex w-full max-w-sm flex-col gap-6 py-6">
      <h1 className="text-2xl font-semibold">{t('invite.title')}</h1>
      {content}
    </div>
  );
}

function Notice({ children }: { children: string }) {
  return <p className="rounded-md border border-border p-3">{children}</p>;
}
