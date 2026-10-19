import {
  keepPreviousData,
  useQuery,
  useQueryClient,
} from '@tanstack/react-query';
import { useState } from 'react';
import { useTranslation } from 'react-i18next';
import { ErrorAlert } from '@/components/feedback/ErrorAlert';
import { useDocumentTitle } from '@/app/useDocumentTitle';
import { useSession } from '@/features/auth/useSession';
import { useFormatters } from '@/lib/formatters';
import { OneTimeLinkPanel } from '../OneTimeLinkPanel';
import { assignableRoles, canAssignRole, roleNameKey } from '../roles';
import {
  createInvitation,
  fetchInvitations,
  INVITATIONS_QUERY_KEY,
  invitationsListKey,
  reissueInvitation,
  type InvitationInput,
  type InvitationIssued,
  type InvitationListItem,
} from './api';
import { InvitationsList, type InvitationsListState } from './InvitationsList';
import { InviteForm } from './InviteForm';

// /invitations (#29 I): invite a person, show the one-time link once, and
// list the open invitations with "New link". The issued link lives only in
// this page's state (I4): never a store, browser storage, the query cache,
// the URL or a log; "Done" or leaving the page drops it.
export function InvitationsPage() {
  const { t } = useTranslation();
  useDocumentTitle(t('invitations.title'));
  const session = useSession();
  const user = session.status === 'signedIn' ? session.user : null;
  const queryClient = useQueryClient();
  const [issued, setIssued] = useState<InvitationIssued | null>(null);
  const [offset, setOffset] = useState(0);
  const [reissuing, setReissuing] = useState<string | null>(null);
  const [reissueError, setReissueError] = useState<unknown>(null);

  const list = useQuery({
    queryKey: invitationsListKey(offset),
    queryFn: ({ signal }) => fetchInvitations(offset, signal),
    placeholderData: keepPreviousData,
  });

  function refreshList() {
    void queryClient.invalidateQueries({ queryKey: INVITATIONS_QUERY_KEY });
  }

  async function invite(input: InvitationInput) {
    const result = await createInvitation(input);
    setReissueError(null);
    setIssued(result);
    refreshList();
  }

  async function reissue(item: InvitationListItem) {
    setReissuing(item.user_id);
    setReissueError(null);
    try {
      setIssued(await reissueInvitation(item.user_id));
      refreshList();
    } catch (caught) {
      setReissueError(caught);
    } finally {
      setReissuing(null);
    }
  }

  const listState: InvitationsListState = list.data
    ? { status: 'ready', page: list.data }
    : list.isError
      ? {
          status: 'error',
          error: list.error,
          onRetry: () => void list.refetch(),
        }
      : { status: 'loading' };

  return (
    <div className="flex flex-col gap-8">
      <div className="flex flex-col gap-2">
        <h1 className="text-2xl font-semibold">{t('invitations.title')}</h1>
        <p>{t('invitations.intro')}</p>
      </div>
      {issued ? (
        <InvitationLinkPanel
          key={issued.invitation_url}
          issued={issued}
          onDone={() => setIssued(null)}
        />
      ) : null}
      <InviteForm roles={assignableRoles(user)} onSubmit={invite} />
      {reissueError ? <ErrorAlert error={reissueError} /> : null}
      <InvitationsList
        state={listState}
        canReissue={(item) =>
          item.roles.every((role) => canAssignRole(user, role))
        }
        onReissue={(item) => void reissue(item)}
        reissuing={reissuing}
        onPage={setOffset}
      />
    </div>
  );
}

/** The one-time link panel with the invitation's details. */
export function InvitationLinkPanel({
  issued,
  onDone,
}: {
  issued: InvitationIssued;
  onDone: () => void;
}) {
  const { t } = useTranslation();
  const { formatInstant } = useFormatters();
  return (
    <OneTimeLinkPanel
      url={issued.invitation_url}
      details={[
        { label: t('invitations.result.person'), value: issued.display_name },
        { label: t('invitations.result.username'), value: issued.username },
        {
          label: t('invitations.result.role'),
          value: issued.roles.map((role) => t(roleNameKey(role))).join(', '),
        },
        {
          label: t('invitations.result.expires'),
          value: formatInstant(issued.expires_at, 'dateTime'),
        },
      ]}
      labels={{
        title: t('invitations.result.title'),
        link: t('invitations.result.link'),
        warning: t('invitations.result.onceWarning'),
        copy: t('invitations.result.copy'),
        copied: t('invitations.result.copied'),
        copyFailed: t('invitations.result.copyFailed'),
        done: t('invitations.result.done'),
      }}
      onDone={onDone}
    />
  );
}
