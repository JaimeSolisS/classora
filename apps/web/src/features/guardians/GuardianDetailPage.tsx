import { useQuery, useQueryClient } from '@tanstack/react-query';
import { useId, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { Link, useLocation, useParams } from 'react-router';
import { ApiError } from '@/app/api';
import { useDocumentTitle } from '@/app/useDocumentTitle';
import { ErrorAlert } from '@/components/feedback/ErrorAlert';
import { LoadingState } from '@/components/feedback/LoadingState';
import { Button } from '@/components/ui/button';
import { useCan } from '@/features/auth/useSession';
import {
  StudentAgeBadge,
  StudentStatusBadge,
} from '@/features/students/StudentBadges';
import { LINK_BUTTON_CLASS, TEXT_LINK_CLASS } from '@/features/students/styles';
import {
  fetchGuardian,
  guardianDetailKey,
  GUARDIANS_LIST_QUERY_KEY,
  updateGuardian,
  type GuardianDetail,
} from './api';
import { ConfirmDialog } from './ConfirmDialog';
import { AccountBadge } from './GuardianBadges';
import type { SavedState } from './GuardianFormPages';
import { fullName } from './rules';

// /guardians/:guardianId (#34 I3): the profile. The heading is the full name
// with a status badge, then the contact section, the account section
// (username, display name and state when the server sent `account`, which
// only family.manage holders get; otherwise "Has account" / "No account")
// and the linked students with their relationship and Minor/Adult badges.
// With family.manage: "Edit" and "Deactivate"/"Reactivate" behind a
// confirmation dialog that sends {version, is_active}. A 404 (missing or not
// visible) says "This guardian doesn't exist.".

export function GuardianDetailPage() {
  const { t } = useTranslation();
  const { guardianId = '' } = useParams();
  const location = useLocation();
  const queryClient = useQueryClient();
  const canManage = useCan('family.manage');
  const key = guardianDetailKey(guardianId);
  const detail = useQuery({
    queryKey: key,
    queryFn: ({ signal }) => fetchGuardian(guardianId, signal),
  });
  useDocumentTitle(detail.data ? fullName(detail.data) : t('guardians.title'));
  const arrivedSaved = (location.state as SavedState | null)?.saved === true;

  async function setActive(active: boolean) {
    const current = queryClient.getQueryData<GuardianDetail>(key);
    if (!current) {
      return;
    }
    const updated = await updateGuardian(guardianId, {
      version: current.version,
      is_active: active,
    });
    queryClient.setQueryData(key, updated);
    void queryClient.invalidateQueries({ queryKey: GUARDIANS_LIST_QUERY_KEY });
  }

  if (detail.isError) {
    const notFound =
      detail.error instanceof ApiError && detail.error.status === 404;
    return (
      <div className="flex flex-col gap-4">
        <h1 className="text-2xl font-semibold">
          {notFound ? t('guardians.notFound') : t('guardians.title')}
        </h1>
        {notFound ? null : (
          <ErrorAlert
            error={detail.error}
            onRetry={() => void detail.refetch()}
          />
        )}
        <p>
          <Link to="/guardians" className={TEXT_LINK_CLASS}>
            {t('guardians.back')}
          </Link>
        </p>
      </div>
    );
  }
  if (!detail.data) {
    return <LoadingState />;
  }
  return (
    <GuardianDetailView
      guardian={detail.data}
      canManage={canManage}
      initiallySaved={arrivedSaved}
      onSetActive={setActive}
      onReload={async () => {
        await detail.refetch();
      }}
    />
  );
}

interface GuardianDetailViewProps {
  guardian: GuardianDetail;
  /** The caller holds family.manage (UX only; the API decides). */
  canManage: boolean;
  /** Arrived from a save: announce "Changes saved.". */
  initiallySaved?: boolean;
  /** Deactivates (false) or reactivates (true); rejects with the error. */
  onSetActive: (active: boolean) => Promise<void>;
  onReload: () => Promise<void>;
}

/** The detail page's content, without any query (stories use it). */
export function GuardianDetailView({
  guardian,
  canManage,
  initiallySaved = false,
  onSetActive,
  onReload,
}: GuardianDetailViewProps) {
  const { t } = useTranslation();
  const id = useId();
  const [saved, setSaved] = useState(initiallySaved);
  const [error, setError] = useState<unknown>(null);
  const [confirming, setConfirming] = useState(false);
  const notSet = t('guardians.notSet');
  const isConflict =
    error instanceof ApiError && error.code === 'version_conflict';
  const account = guardian.account;

  async function confirm() {
    setError(null);
    setSaved(false);
    try {
      await onSetActive(!guardian.is_active);
      setSaved(true);
    } catch (caught) {
      setError(caught);
    } finally {
      setConfirming(false);
    }
  }

  return (
    <div className="flex flex-col gap-8">
      <div className="flex flex-col gap-3">
        <p>
          <Link to="/guardians" className={TEXT_LINK_CLASS}>
            {t('guardians.back')}
          </Link>
        </p>
        <h1 className="text-2xl font-semibold break-words">
          {fullName(guardian)}
        </h1>
        <p className="flex flex-wrap gap-2">
          <StudentStatusBadge active={guardian.is_active} />
        </p>
        {canManage ? (
          <div className="flex flex-wrap items-center gap-3">
            <Link
              to={`/guardians/${guardian.id}/edit`}
              className={LINK_BUTTON_CLASS}
            >
              {t('guardians.edit')}
            </Link>
            <Button
              type="button"
              variant={guardian.is_active ? 'outline' : 'default'}
              onClick={() => {
                setError(null);
                setSaved(false);
                setConfirming(true);
              }}
            >
              {guardian.is_active
                ? t('guardians.deactivate.action')
                : t('guardians.reactivate.action')}
            </Button>
          </div>
        ) : null}
        {error ? (
          <ErrorAlert
            error={error}
            onRetry={
              isConflict
                ? () => {
                    setError(null);
                    void onReload();
                  }
                : undefined
            }
            retryLabel={isConflict ? t('guardians.conflict.reload') : undefined}
          />
        ) : null}
        <p role="status" className="text-sm">
          {saved ? t('guardians.saved') : null}
        </p>
      </div>

      <section
        aria-labelledby={`${id}-contact`}
        className="flex flex-col gap-3"
      >
        <h2 id={`${id}-contact`} className="text-lg font-semibold">
          {t('guardians.sections.contact')}
        </h2>
        <dl className="grid grid-cols-[auto_1fr] gap-x-4 gap-y-2 text-sm">
          <dt className="font-medium">{t('guardians.details.email')}</dt>
          <dd className="min-w-0 break-words">{guardian.email ?? notSet}</dd>
          <dt className="font-medium">{t('guardians.details.phone')}</dt>
          <dd className="min-w-0 break-words">
            {guardian.phone ? (
              <a href={`tel:${guardian.phone}`} className={TEXT_LINK_CLASS}>
                {guardian.phone}
              </a>
            ) : (
              notSet
            )}
          </dd>
        </dl>
      </section>

      <section
        aria-labelledby={`${id}-account`}
        className="flex flex-col gap-3"
      >
        <h2 id={`${id}-account`} className="text-lg font-semibold">
          {t('guardians.sections.account')}
        </h2>
        {account ? (
          <dl className="grid grid-cols-[auto_1fr] gap-x-4 gap-y-2 text-sm">
            <dt className="font-medium">{t('guardians.details.username')}</dt>
            <dd className="min-w-0 break-words">{account.username}</dd>
            <dt className="font-medium">
              {t('guardians.details.displayName')}
            </dt>
            <dd className="min-w-0 break-words">{account.display_name}</dd>
            <dt className="font-medium">{t('guardians.details.state')}</dt>
            <dd>{t(`userStates.${account.state}`)}</dd>
          </dl>
        ) : (
          <p>
            <AccountBadge hasAccount={guardian.has_account} />
          </p>
        )}
      </section>

      <section
        aria-labelledby={`${id}-students`}
        className="flex flex-col gap-3"
      >
        <h2 id={`${id}-students`} className="text-lg font-semibold">
          {t('guardians.students.title')}
        </h2>
        {guardian.students.length === 0 ? (
          <p className="text-sm">{t('guardians.students.empty')}</p>
        ) : (
          <ol className="flex flex-col gap-2 text-sm">
            {guardian.students.map((student) => (
              <li
                key={student.student_id}
                className="flex flex-wrap items-center gap-2 break-words"
              >
                <Link
                  to={`/students/${student.student_id}`}
                  className={TEXT_LINK_CLASS}
                >
                  {fullName(student)}
                </Link>
                <span>· {t(`relationships.${student.relationship}`)}</span>
                <StudentAgeBadge minor={student.is_minor} />
                {student.is_active ? null : (
                  <StudentStatusBadge active={false} />
                )}
              </li>
            ))}
          </ol>
        )}
      </section>

      <ConfirmDialog
        open={confirming}
        title={
          guardian.is_active
            ? t('guardians.deactivate.title')
            : t('guardians.reactivate.title')
        }
        body={
          guardian.is_active
            ? t('guardians.deactivate.body')
            : t('guardians.reactivate.body')
        }
        confirmLabel={
          guardian.is_active
            ? t('guardians.deactivate.action')
            : t('guardians.reactivate.action')
        }
        destructive={guardian.is_active}
        onConfirm={confirm}
        onCancel={() => setConfirming(false)}
      />
    </div>
  );
}
