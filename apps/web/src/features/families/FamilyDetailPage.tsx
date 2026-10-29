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
import { ConfirmDialog } from '@/features/guardians/ConfirmDialog';
import { AccountBadge } from '@/features/guardians/GuardianBadges';
import {
  GUARDIANS_QUERY_KEY,
  searchActiveGuardians,
  type GuardianListItem,
} from '@/features/guardians/api';
import { fullName } from '@/features/guardians/rules';
import { STUDENTS_QUERY_KEY } from '@/features/students/api';
import {
  StudentAgeBadge,
  StudentStatusBadge,
} from '@/features/students/StudentBadges';
import { LINK_BUTTON_CLASS, TEXT_LINK_CLASS } from '@/features/students/styles';
import { AddMemberDialog } from './AddMemberDialog';
import {
  addMember,
  FAMILIES_LIST_QUERY_KEY,
  familyDetailKey,
  fetchFamily,
  removeMember,
  searchStudents,
  updateFamily,
  type FamilyDetail,
  type MemberKind,
  type StudentOption,
} from './api';
import { FamilyStatusBadge } from './FamilyBadges';
import type { SavedState } from './FamilyFormPages';

// /families/:familyId (#172 H3): the heading is the family name with a
// status badge, then the Students section (links to the students, with
// Minor/Adult badges) and the Guardians section (links to the guardians,
// phone as tel:, account badge). A guardian viewer gets only their own
// profile in Guardians (the server decides) and no controls. With
// family.manage: "Add student" / "Add guardian" dialogs (the #34 picker),
// "Remove" per row behind a confirmation, "Edit", and "Deactivate" /
// "Reactivate" behind a confirmation. With billing.view, a "Billing
// account" link (#379 A3). A 404 (missing or not visible) says
// "This family doesn't exist.".

export function FamilyDetailPage() {
  const { t } = useTranslation();
  const { familyId = '' } = useParams();
  const location = useLocation();
  const queryClient = useQueryClient();
  const canManage = useCan('family.manage');
  const canOpenBilling = useCan('billing.view');
  const key = familyDetailKey(familyId);
  const detail = useQuery({
    queryKey: key,
    queryFn: ({ signal }) => fetchFamily(familyId, signal),
  });
  useDocumentTitle(detail.data ? detail.data.name : t('families.title'));
  const arrivedSaved = (location.state as SavedState | null)?.saved === true;

  async function setActive(active: boolean) {
    const current = queryClient.getQueryData<FamilyDetail>(key);
    if (!current) {
      return;
    }
    const updated = await updateFamily(familyId, {
      version: current.version,
      is_active: active,
    });
    queryClient.setQueryData(key, updated);
    void queryClient.invalidateQueries({ queryKey: FAMILIES_LIST_QUERY_KEY });
  }

  /** After a membership change: the detail, the list and the member pages
   * (their Family line) are stale. */
  async function refresh() {
    void queryClient.invalidateQueries({ queryKey: FAMILIES_LIST_QUERY_KEY });
    void queryClient.invalidateQueries({ queryKey: STUDENTS_QUERY_KEY });
    void queryClient.invalidateQueries({ queryKey: GUARDIANS_QUERY_KEY });
    await detail.refetch();
  }

  if (detail.isError) {
    const notFound =
      detail.error instanceof ApiError && detail.error.status === 404;
    return (
      <div className="flex flex-col gap-4">
        <h1 className="text-2xl font-semibold">
          {notFound ? t('families.notFound') : t('families.title')}
        </h1>
        {notFound ? null : (
          <ErrorAlert
            error={detail.error}
            onRetry={() => void detail.refetch()}
          />
        )}
        <p>
          <Link to="/families" className={TEXT_LINK_CLASS}>
            {t('families.back')}
          </Link>
        </p>
      </div>
    );
  }
  if (!detail.data) {
    return <LoadingState />;
  }
  return (
    <FamilyDetailView
      family={detail.data}
      canManage={canManage}
      canOpenBilling={canOpenBilling}
      initiallySaved={arrivedSaved}
      onSetActive={setActive}
      onReload={async () => {
        await detail.refetch();
      }}
      onAdd={async (kind, memberId) => {
        await addMember(familyId, kind, memberId);
        await refresh();
      }}
      onRemove={async (kind, memberId) => {
        await removeMember(familyId, kind, memberId);
        await refresh();
      }}
    />
  );
}

interface FamilyDetailViewProps {
  family: FamilyDetail;
  /** The caller holds family.manage (UX only; the API decides). */
  canManage: boolean;
  /** billing.view: a link to the family's billing account (#379 A3). */
  canOpenBilling?: boolean;
  /** Arrived from a save: announce "Changes saved.". */
  initiallySaved?: boolean;
  /** Deactivates (false) or reactivates (true); rejects with the error. */
  onSetActive: (active: boolean) => Promise<void>;
  onReload: () => Promise<void>;
  /** Adds a member; rejects with the error to show in the dialog. */
  onAdd: (kind: MemberKind, memberId: string) => Promise<void>;
  /** Removes a member; rejects with the error to show. */
  onRemove: (kind: MemberKind, memberId: string) => Promise<void>;
  /** The pickers' searches (stories pass stubs). */
  searchStudentOptions?: (
    q: string,
    signal: AbortSignal,
  ) => Promise<StudentOption[]>;
  searchGuardianOptions?: (
    q: string,
    signal: AbortSignal,
  ) => Promise<GuardianListItem[]>;
}

interface Removing {
  kind: MemberKind;
  id: string;
  name: string;
}

/** The detail page's content, without any query (stories use it). */
export function FamilyDetailView({
  family,
  canManage,
  canOpenBilling = false,
  initiallySaved = false,
  onSetActive,
  onReload,
  onAdd,
  onRemove,
  searchStudentOptions = searchStudents,
  searchGuardianOptions = searchActiveGuardians,
}: FamilyDetailViewProps) {
  const { t } = useTranslation();
  const id = useId();
  const [announcement, setAnnouncement] = useState(
    initiallySaved ? t('families.saved') : '',
  );
  const [error, setError] = useState<unknown>(null);
  const [confirming, setConfirming] = useState(false);
  const [adding, setAdding] = useState<MemberKind | null>(null);
  const [removing, setRemoving] = useState<Removing | null>(null);
  const isConflict =
    error instanceof ApiError && error.code === 'version_conflict';

  function clear() {
    setError(null);
    setAnnouncement('');
  }

  async function confirmActive() {
    clear();
    try {
      await onSetActive(!family.is_active);
      setAnnouncement(t('families.saved'));
    } catch (caught) {
      setError(caught);
    } finally {
      setConfirming(false);
    }
  }

  async function confirmRemove() {
    if (!removing) {
      return;
    }
    clear();
    try {
      await onRemove(removing.kind, removing.id);
      setAnnouncement(t(`families.${removing.kind}.removed`));
    } catch (caught) {
      setError(caught);
    } finally {
      setRemoving(null);
    }
  }

  function removeButton(kind: MemberKind, memberId: string, name: string) {
    if (!canManage) {
      return null;
    }
    return (
      <Button
        type="button"
        variant="outline"
        size="sm"
        aria-label={t('families.removeNamed', { name })}
        onClick={() => {
          clear();
          setRemoving({ kind, id: memberId, name });
        }}
      >
        {t('families.remove')}
      </Button>
    );
  }

  function sectionHeading(kind: MemberKind) {
    return (
      <div className="flex flex-wrap items-center justify-between gap-3">
        <h2 id={`${id}-${kind}`} className="text-lg font-semibold">
          {t(`families.${kind}.title`)}
        </h2>
        {canManage ? (
          <Button
            type="button"
            variant="outline"
            onClick={() => {
              clear();
              setAdding(kind);
            }}
          >
            {t(`families.${kind}.add`)}
          </Button>
        ) : null}
      </div>
    );
  }

  return (
    <div className="flex flex-col gap-8">
      <div className="flex flex-col gap-3">
        <p>
          <Link to="/families" className={TEXT_LINK_CLASS}>
            {t('families.back')}
          </Link>
        </p>
        <h1 className="text-2xl font-semibold break-words">{family.name}</h1>
        <p className="flex flex-wrap gap-2">
          <FamilyStatusBadge active={family.is_active} />
        </p>
        {canOpenBilling ? (
          <p>
            <Link
              to={`/billing/accounts/${family.id}`}
              className={TEXT_LINK_CLASS}
            >
              {t('families.billingAccount')}
            </Link>
          </p>
        ) : null}
        {canManage ? (
          <div className="flex flex-wrap items-center gap-3">
            <Link
              to={`/families/${family.id}/edit`}
              className={LINK_BUTTON_CLASS}
            >
              {t('families.edit')}
            </Link>
            <Button
              type="button"
              variant={family.is_active ? 'outline' : 'default'}
              onClick={() => {
                clear();
                setConfirming(true);
              }}
            >
              {family.is_active
                ? t('families.deactivate.action')
                : t('families.reactivate.action')}
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
            retryLabel={isConflict ? t('families.conflict.reload') : undefined}
          />
        ) : null}
        <p role="status" className="text-sm">
          {announcement}
        </p>
      </div>

      <section
        aria-labelledby={`${id}-students`}
        className="flex flex-col gap-3"
      >
        {sectionHeading('students')}
        {family.students.length === 0 ? (
          <p className="text-sm">{t('families.students.empty')}</p>
        ) : (
          <ul className="flex flex-col gap-3 text-sm">
            {family.students.map((student) => (
              <li
                key={student.student_id}
                className="flex flex-wrap items-center gap-x-3 gap-y-2 break-words"
              >
                <Link
                  to={`/students/${student.student_id}`}
                  className={TEXT_LINK_CLASS}
                >
                  {fullName(student)}
                </Link>
                <StudentAgeBadge minor={student.is_minor} />
                {student.is_active ? null : (
                  <StudentStatusBadge active={false} />
                )}
                {removeButton(
                  'students',
                  student.student_id,
                  fullName(student),
                )}
              </li>
            ))}
          </ul>
        )}
      </section>

      <section
        aria-labelledby={`${id}-guardians`}
        className="flex flex-col gap-3"
      >
        {sectionHeading('guardians')}
        {family.guardians.length === 0 ? (
          <p className="text-sm">{t('families.guardians.empty')}</p>
        ) : (
          <ul className="flex flex-col gap-3 text-sm">
            {family.guardians.map((guardian) => (
              <li
                key={guardian.guardian_id}
                className="flex flex-wrap items-center gap-x-3 gap-y-2 break-words"
              >
                <Link
                  to={`/guardians/${guardian.guardian_id}`}
                  className={TEXT_LINK_CLASS}
                >
                  {fullName(guardian)}
                </Link>
                {guardian.phone ? (
                  <a href={`tel:${guardian.phone}`} className={TEXT_LINK_CLASS}>
                    {guardian.phone}
                  </a>
                ) : null}
                <AccountBadge hasAccount={guardian.has_account} />
                {guardian.is_active ? null : (
                  <StudentStatusBadge active={false} />
                )}
                {removeButton(
                  'guardians',
                  guardian.guardian_id,
                  fullName(guardian),
                )}
              </li>
            ))}
          </ul>
        )}
      </section>

      <AddMemberDialog<StudentOption>
        kind="students"
        open={adding === 'students'}
        search={searchStudentOptions}
        onCancel={() => setAdding(null)}
        onAdd={async (memberId) => {
          await onAdd('students', memberId);
          setAdding(null);
          setAnnouncement(t('families.students.added'));
        }}
      />
      <AddMemberDialog<GuardianListItem>
        kind="guardians"
        open={adding === 'guardians'}
        search={searchGuardianOptions}
        onCancel={() => setAdding(null)}
        onAdd={async (memberId) => {
          await onAdd('guardians', memberId);
          setAdding(null);
          setAnnouncement(t('families.guardians.added'));
        }}
      />
      <ConfirmDialog
        open={removing !== null}
        title={t('families.removeTitle')}
        body={t('families.removeBody', { name: removing?.name ?? '' })}
        confirmLabel={t('families.remove')}
        destructive
        onConfirm={confirmRemove}
        onCancel={() => setRemoving(null)}
      />
      <ConfirmDialog
        open={confirming}
        title={
          family.is_active
            ? t('families.deactivate.title')
            : t('families.reactivate.title')
        }
        body={
          family.is_active
            ? t('families.deactivate.body')
            : t('families.reactivate.body')
        }
        confirmLabel={
          family.is_active
            ? t('families.deactivate.action')
            : t('families.reactivate.action')
        }
        destructive={family.is_active}
        onConfirm={confirmActive}
        onCancel={() => setConfirming(false)}
      />
    </div>
  );
}
