import { useTranslation } from 'react-i18next';
import { ErrorAlert } from '@/components/feedback/ErrorAlert';
import { LoadingState } from '@/components/feedback/LoadingState';
import { PublicPageSignedInNotice } from '../PublicPageSignedInNotice';
import { SessionLoading } from '../SessionLoading';
import { RedeemForm } from './RedeemForm';

// A one-time link page in each of its states (#29 J for /invite, #30 J for
// /reset), without any API call: RedeemLinkPage decides the state, stories
// pass one directly. The page-specific text comes in `copy`.

export type RedeemLinkViewState =
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

/** The text that differs between the link pages (already translated). */
export interface RedeemLinkCopy {
  /** The page heading and document title. */
  title: string;
  /** The signed-in notice for `name`. */
  signedIn: (name: string) => string;
  /** The line above the username, for the person's display name. */
  intro: (displayName: string) => string;
}

export function RedeemLinkView({
  state,
  copy,
}: {
  state: RedeemLinkViewState;
  copy: RedeemLinkCopy;
}) {
  const { t } = useTranslation();
  const { t: tErrors } = useTranslation('errors');

  if (state.kind === 'sessionLoading') {
    return <SessionLoading />;
  }
  if (state.kind === 'signedIn') {
    return (
      <PublicPageSignedInNotice
        title={copy.title}
        message={copy.signedIn(state.name)}
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
          intro={copy.intro(state.displayName)}
          onSubmit={state.onSubmit}
        />
      );
      break;
  }

  return (
    <div className="mx-auto flex w-full max-w-sm flex-col gap-6 py-6">
      <h1 className="text-2xl font-semibold">{copy.title}</h1>
      {content}
    </div>
  );
}

function Notice({ children }: { children: string }) {
  return <p className="rounded-md border border-border p-3">{children}</p>;
}

/** The /invite text (#29). */
function useInviteCopy(): RedeemLinkCopy {
  const { t } = useTranslation();
  return {
    title: t('invite.title'),
    signedIn: (name) => t('invite.signedIn', { name }),
    intro: (name) => t('invite.welcome', { name }),
  };
}

/** The /reset text (#30). */
function useResetCopy(): RedeemLinkCopy {
  const { t } = useTranslation();
  return {
    title: t('reset.title'),
    signedIn: (name) => t('reset.signedIn', { name }),
    intro: () => t('reset.username'),
  };
}

/** The /invite page view (#29 J). */
export function InviteView({ state }: { state: RedeemLinkViewState }) {
  return <RedeemLinkView state={state} copy={useInviteCopy()} />;
}

/** The /reset page view (#30 J). */
export function ResetView({ state }: { state: RedeemLinkViewState }) {
  return <RedeemLinkView state={state} copy={useResetCopy()} />;
}
