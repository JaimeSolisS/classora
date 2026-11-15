import { useState } from 'react';
import { useTranslation } from 'react-i18next';
import { useDocumentTitle } from '@/app/useDocumentTitle';
import { useSession } from '@/features/auth/useSession';
import { useFormatters } from '@/lib/formatters';
import { OneTimeLinkPanel } from '../OneTimeLinkPanel';
import { issuePasswordReset, type PasswordResetIssued } from './api';
import { PasswordResetForm } from './PasswordResetForm';

// /password-resets (#30 I): an Owner or Administrator enters a username,
// confirms their password in the reauthentication dialog and gets a one-time
// reset link, shown once. The link lives only in this page's state (I5):
// never a store, browser storage, the query cache, the URL or a log; "Done"
// or leaving the page drops it.
export function PasswordResetsPage() {
  const { t } = useTranslation();
  useDocumentTitle(t('passwordResets.title'));
  const session = useSession();
  const ownUsername =
    session.status === 'signedIn' ? session.user.username : '';
  const [issued, setIssued] = useState<PasswordResetIssued | null>(null);

  async function issue(username: string) {
    setIssued(await issuePasswordReset(username));
  }

  return (
    <div className="flex flex-col gap-8">
      <div className="flex flex-col gap-2">
        <h1 className="text-2xl font-semibold">{t('passwordResets.title')}</h1>
        <p>{t('passwordResets.intro')}</p>
      </div>
      {issued ? (
        <PasswordResetLinkPanel
          key={issued.reset_url}
          issued={issued}
          onDone={() => setIssued(null)}
        />
      ) : null}
      <PasswordResetForm ownUsername={ownUsername} onSubmit={issue} />
    </div>
  );
}

/** The one-time link panel with the reset's details. */
export function PasswordResetLinkPanel({
  issued,
  onDone,
}: {
  issued: PasswordResetIssued;
  onDone: () => void;
}) {
  const { t } = useTranslation();
  const { formatInstant } = useFormatters();
  return (
    <OneTimeLinkPanel
      url={issued.reset_url}
      details={[
        { label: t('oneTimeLink.person'), value: issued.display_name },
        { label: t('oneTimeLink.username'), value: issued.username },
        {
          label: t('oneTimeLink.expires'),
          value: formatInstant(issued.expires_at, 'dateTime'),
        },
      ]}
      labels={{
        title: t('passwordResets.result.title'),
        link: t('passwordResets.result.link'),
        warning: t('oneTimeLink.onceWarning'),
        copy: t('oneTimeLink.copy'),
        copied: t('oneTimeLink.copied'),
        copyFailed: t('oneTimeLink.copyFailed'),
        done: t('oneTimeLink.done'),
      }}
      onDone={onDone}
    />
  );
}
