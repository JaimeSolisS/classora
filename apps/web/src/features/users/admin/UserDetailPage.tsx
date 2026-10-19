import { useQuery, useQueryClient } from '@tanstack/react-query';
import { useId, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { Link, useParams } from 'react-router';
import { ApiError } from '@/app/api';
import { useDocumentTitle } from '@/app/useDocumentTitle';
import { ErrorAlert } from '@/components/feedback/ErrorAlert';
import { LoadingState } from '@/components/feedback/LoadingState';
import { Spinner } from '@/components/feedback/Spinner';
import { Button } from '@/components/ui/button';
import { loginNoticeState } from '@/features/auth/loginNotice';
import type { RoleCode } from '@/features/auth/session';
import { useAuth } from '@/features/auth/useSession';
import {
  issuePasswordReset,
  type PasswordResetIssued,
} from '../password-resets/api';
import { PasswordResetLinkPanel } from '../password-resets/PasswordResetsPage';
import { roleNameKey } from '../roles';
import { AccountStatus } from './AccountStatus';
import {
  changeUserState,
  fetchUser,
  updateUser,
  updateUserDelegations,
  updateUserRoles,
  userDetailKey,
  USERS_LIST_QUERY_KEY,
  type DelegablePermission,
  type StateAction,
  type UserDetail,
  type UserUpdate,
} from './api';
import { DelegationsEditor } from './DelegationsEditor';
import { ProfileForm, ProfileReadOnly } from './ProfileForm';
import { RolesEditor } from './RolesEditor';
import { hasStateActions } from './rules';
import { StateBadge } from './StateBadge';

// /users/:userId (#31 K, #170 G): the account of one user. The header shows
// the name, username, state and current roles. Each section appears only
// when the server's `allowed_actions` allow it: the profile form (else the
// profile as text), the roles editor (`assignable_roles` not empty), the
// delegated permissions (only for an Administrator; editable with
// `manage_delegations`), the account status buttons, and "Create password
// reset link" (the #30 route; the link lives only in this page's state and is
// never cached). A successful change replaces the cached detail with the
// response and refreshes the list. Changing your own roles ends your session
// on the server, so the app signs out locally and goes to /login (G4).

const LINK_CLASS =
  'font-medium underline underline-offset-4 outline-none focus-visible:ring-3 focus-visible:ring-ring';

export function UserDetailPage() {
  const { t } = useTranslation();
  const { userId = '' } = useParams();
  const queryClient = useQueryClient();
  const { session, signOutLocally } = useAuth();
  const key = userDetailKey(userId);
  const detail = useQuery({
    queryKey: key,
    queryFn: ({ signal }) => fetchUser(userId, signal),
  });
  useDocumentTitle(detail.data?.display_name ?? t('users.title'));

  function store(user: UserDetail) {
    queryClient.setQueryData(key, user);
    void queryClient.invalidateQueries({ queryKey: USERS_LIST_QUERY_KEY });
  }

  async function saveProfile(body: UserUpdate) {
    store(await updateUser(userId, body));
  }

  async function changeState(action: StateAction) {
    const current = queryClient.getQueryData<UserDetail>(key);
    if (!current) {
      return;
    }
    store(await changeUserState(userId, action, current.version));
  }

  function loadedVersion(): number | null {
    return queryClient.getQueryData<UserDetail>(key)?.version ?? null;
  }

  async function saveRoles(roles: RoleCode[]) {
    const version = loadedVersion();
    if (version === null) {
      return;
    }
    const updated = await updateUserRoles(userId, roles, version);
    if (
      session.status === 'signedIn' &&
      updated.id === session.user.id &&
      updated.username === session.user.username
    ) {
      // The server revoked every session of ours (#25): no /auth/logout.
      signOutLocally(loginNoticeState('roles_changed', updated.username));
      return;
    }
    store(updated);
  }

  async function saveDelegations(permissions: DelegablePermission[]) {
    const version = loadedVersion();
    if (version === null) {
      return;
    }
    store(await updateUserDelegations(userId, permissions, version));
  }

  async function reload() {
    await detail.refetch();
  }

  if (detail.isError) {
    const notFound =
      detail.error instanceof ApiError && detail.error.status === 404;
    return (
      <div className="flex flex-col gap-4">
        <h1 className="text-2xl font-semibold">
          {notFound ? t('users.detail.notFound') : t('users.title')}
        </h1>
        {notFound ? null : (
          <ErrorAlert error={detail.error} onRetry={() => void reload()} />
        )}
        <p>
          <Link to="/users" className={LINK_CLASS}>
            {t('users.detail.backToList')}
          </Link>
        </p>
      </div>
    );
  }
  if (!detail.data) {
    return <LoadingState />;
  }
  const user = detail.data;
  return (
    <UserDetailView
      user={user}
      ownRecord={session.status === 'signedIn' && session.user.id === user.id}
      onSaveProfile={saveProfile}
      onSaveRoles={saveRoles}
      onSaveDelegations={saveDelegations}
      onChangeState={changeState}
      onReload={reload}
    />
  );
}

interface UserDetailViewProps {
  user: UserDetail;
  /** The signed-in user's own record (#170 G4). */
  ownRecord?: boolean;
  onSaveProfile: (body: UserUpdate) => Promise<void>;
  onSaveRoles: (roles: RoleCode[]) => Promise<void>;
  onSaveDelegations: (permissions: DelegablePermission[]) => Promise<void>;
  onChangeState: (action: StateAction) => Promise<void>;
  onReload: () => Promise<void>;
  /** Issues a reset link (default: the #30 route through apiCall). */
  onIssueReset?: (username: string) => Promise<PasswordResetIssued>;
}

/** The detail page's content, without any query (stories use it). */
export function UserDetailView({
  user,
  ownRecord = false,
  onSaveProfile,
  onSaveRoles,
  onSaveDelegations,
  onChangeState,
  onReload,
  onIssueReset = issuePasswordReset,
}: UserDetailViewProps) {
  const { t } = useTranslation();
  const pendingNote =
    user.state === 'pending'
      ? user.password_set
        ? t('users.detail.pendingRequest')
        : t('users.detail.pendingInvitation')
      : null;
  return (
    <div className="flex flex-col gap-8">
      <div className="flex flex-col gap-3">
        <p>
          <Link to="/users" className={LINK_CLASS}>
            {t('users.detail.backToList')}
          </Link>
        </p>
        <h1 className="text-2xl font-semibold break-words">
          {user.display_name}
        </h1>
        <dl className="grid grid-cols-[auto_1fr] items-center gap-x-4 gap-y-2 text-sm">
          <dt className="font-medium">{t('users.detail.username')}</dt>
          <dd className="min-w-0 break-words">{user.username}</dd>
          <dt className="font-medium">{t('users.detail.state')}</dt>
          <dd className="flex flex-wrap items-center gap-2">
            <StateBadge state={user.state} />
            {pendingNote ? <span>{pendingNote}</span> : null}
          </dd>
          <dt className="font-medium">{t('users.detail.roles')}</dt>
          <dd>
            {user.roles.length > 0
              ? user.roles.map((role) => t(roleNameKey(role))).join(', ')
              : t('users.detail.noRoles')}
          </dd>
        </dl>
      </div>
      {user.allowed_actions.edit ? (
        <ProfileForm user={user} onSubmit={onSaveProfile} onReload={onReload} />
      ) : (
        <ProfileReadOnly user={user} />
      )}
      {user.allowed_actions.assignable_roles.length > 0 ? (
        <RolesEditor
          user={user}
          ownRecord={ownRecord}
          onSave={onSaveRoles}
          onReload={onReload}
        />
      ) : null}
      {user.roles.includes('administrator') ? (
        <DelegationsEditor
          user={user}
          onSave={onSaveDelegations}
          onReload={onReload}
        />
      ) : null}
      {hasStateActions(user) ? (
        <AccountStatus
          user={user}
          onChange={onChangeState}
          onReload={onReload}
        />
      ) : null}
      {user.allowed_actions.reset_password ? (
        <ResetLinkSection
          key={user.id}
          username={user.username}
          onIssue={onIssueReset}
        />
      ) : null}
    </div>
  );
}

/** "Create password reset link" and the one-time link panel (#31 K5). */
function ResetLinkSection({
  username,
  onIssue,
}: {
  username: string;
  onIssue: (username: string) => Promise<PasswordResetIssued>;
}) {
  const { t } = useTranslation();
  const id = useId();
  const [issued, setIssued] = useState<PasswordResetIssued | null>(null);
  const [error, setError] = useState<unknown>(null);
  const [sending, setSending] = useState(false);

  async function issue() {
    if (sending) {
      return;
    }
    setSending(true);
    setError(null);
    try {
      setIssued(await onIssue(username));
    } catch (caught) {
      setError(caught);
    } finally {
      setSending(false);
    }
  }

  return (
    <section aria-labelledby={`${id}-title`} className="flex flex-col gap-4">
      <h2 id={`${id}-title`} className="text-lg font-semibold">
        {t('users.reset.title')}
      </h2>
      <p className="text-sm">{t('users.reset.intro')}</p>
      {error ? <ErrorAlert error={error} /> : null}
      {issued ? (
        <PasswordResetLinkPanel
          key={issued.reset_url}
          issued={issued}
          onDone={() => setIssued(null)}
        />
      ) : (
        <div>
          <Button
            type="button"
            variant="outline"
            disabled={sending}
            onClick={() => void issue()}
          >
            {sending ? (
              <Spinner label={t('users.reset.create')} showLabel />
            ) : (
              t('users.reset.create')
            )}
          </Button>
        </div>
      )}
    </section>
  );
}
