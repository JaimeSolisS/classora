import { useQuery, useQueryClient } from '@tanstack/react-query';
import { useId, useRef, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { Link, useLocation, useParams } from 'react-router';
import { ApiError } from '@/app/api';
import { useDocumentTitle } from '@/app/useDocumentTitle';
import { ErrorAlert } from '@/components/feedback/ErrorAlert';
import { LoadingState } from '@/components/feedback/LoadingState';
import { Spinner } from '@/components/feedback/Spinner';
import { Button } from '@/components/ui/button';
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog';
import { useCan } from '@/features/auth/useSession';
import {
  linkGuardian,
  unlinkGuardian,
  type GuardianListItem,
  type StudentGuardianCreate,
} from '@/features/guardians/api';
import { FamilyLine } from '@/features/families/FamilyLine';
import { StudentGuardiansSection } from '@/features/guardians/StudentGuardiansSection';
import { useFormatters } from '@/lib/formatters';
import {
  fetchStudent,
  STUDENTS_LIST_QUERY_KEY,
  studentDetailKey,
  updateStudent,
  type StudentDetail,
} from './api';
import { fullName } from './rules';
import { StudentAgeBadge, StudentStatusBadge } from './StudentBadges';
import type { SavedState } from './StudentFormPages';
import { LINK_BUTTON_CLASS, TEXT_LINK_CLASS } from './styles';
import { useLevelLabel } from './useLevelLabel';

// /students/:studentId (#33 J3): the profile. The heading is "Given Family",
// badges show status and minor/adult, then the school section and, only when
// the server sent `personal` (full access), the personal details and the
// emergency contacts. A limited view has no trace of those sections (no
// "hidden" placeholder). With student.manage: "Edit" and "Deactivate" /
// "Reactivate" behind a confirmation dialog that sends {version, is_active}.
// A 404 (missing or not visible) says "This student doesn't exist.".
// When the server sent `guardians` (staff with guardian.view, #34 I4), the
// Guardians section follows; family.manage holders link and unlink there,
// and the detail is refetched after either.

export function StudentDetailPage() {
  const { t } = useTranslation();
  const { studentId = '' } = useParams();
  const location = useLocation();
  const queryClient = useQueryClient();
  const canManage = useCan('student.manage');
  const canManageFamilies = useCan('family.manage');
  const key = studentDetailKey(studentId);
  const detail = useQuery({
    queryKey: key,
    queryFn: ({ signal }) => fetchStudent(studentId, signal),
  });
  useDocumentTitle(detail.data ? fullName(detail.data) : t('students.title'));
  const arrivedSaved = (location.state as SavedState | null)?.saved === true;

  async function setActive(active: boolean) {
    const current = queryClient.getQueryData<StudentDetail>(key);
    if (!current) {
      return;
    }
    const updated = await updateStudent(studentId, {
      version: current.version,
      is_active: active,
    });
    queryClient.setQueryData(key, updated);
    void queryClient.invalidateQueries({ queryKey: STUDENTS_LIST_QUERY_KEY });
  }

  if (detail.isError) {
    const notFound =
      detail.error instanceof ApiError && detail.error.status === 404;
    return (
      <div className="flex flex-col gap-4">
        <h1 className="text-2xl font-semibold">
          {notFound ? t('students.notFound') : t('students.title')}
        </h1>
        {notFound ? null : (
          <ErrorAlert
            error={detail.error}
            onRetry={() => void detail.refetch()}
          />
        )}
        <p>
          <Link to="/students" className={TEXT_LINK_CLASS}>
            {t('students.back')}
          </Link>
        </p>
      </div>
    );
  }
  if (!detail.data) {
    return <LoadingState />;
  }
  return (
    <StudentDetailView
      student={detail.data}
      canManage={canManage}
      initiallySaved={arrivedSaved}
      onSetActive={setActive}
      onReload={async () => {
        await detail.refetch();
      }}
      canManageGuardians={canManageFamilies}
      onLinkGuardian={async (body) => {
        await linkGuardian(studentId, body);
        await detail.refetch();
      }}
      onUnlinkGuardian={async (guardianId) => {
        await unlinkGuardian(studentId, guardianId);
        await detail.refetch();
      }}
    />
  );
}

interface StudentDetailViewProps {
  student: StudentDetail;
  /** The caller holds student.manage (UX only; the API decides). */
  canManage: boolean;
  /** Arrived from a save: announce "Changes saved.". */
  initiallySaved?: boolean;
  /** Deactivates (false) or reactivates (true); rejects with the error. */
  onSetActive: (active: boolean) => Promise<void>;
  onReload: () => Promise<void>;
  /** The caller holds family.manage: link and unlink guardians. */
  canManageGuardians?: boolean;
  /** Links a guardian, then refreshes; rejects with the error to show. */
  onLinkGuardian?: (body: StudentGuardianCreate) => Promise<void>;
  /** Unlinks a guardian, then refreshes; rejects with the error to show. */
  onUnlinkGuardian?: (guardianId: string) => Promise<void>;
  /** The link dialog's guardian search (stories pass a stub). */
  searchGuardians?: (
    q: string,
    signal: AbortSignal,
  ) => Promise<GuardianListItem[]>;
}

const NO_CHANGE = () => Promise.resolve();

/** The detail page's content, without any query (stories use it). */
export function StudentDetailView({
  student,
  canManage,
  initiallySaved = false,
  onSetActive,
  onReload,
  canManageGuardians = false,
  onLinkGuardian = NO_CHANGE,
  onUnlinkGuardian = NO_CHANGE,
  searchGuardians,
}: StudentDetailViewProps) {
  const { t } = useTranslation();
  const { formatLocalDate } = useFormatters();
  const levelLabel = useLevelLabel();
  const id = useId();
  const [saved, setSaved] = useState(initiallySaved);
  const [error, setError] = useState<unknown>(null);
  const [confirming, setConfirming] = useState(false);
  const notSet = t('students.notSet');
  const personal = student.personal;
  const isConflict =
    error instanceof ApiError && error.code === 'version_conflict';

  async function confirm() {
    setError(null);
    setSaved(false);
    try {
      await onSetActive(!student.is_active);
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
          <Link to="/students" className={TEXT_LINK_CLASS}>
            {t('students.back')}
          </Link>
        </p>
        <h1 className="text-2xl font-semibold break-words">
          {fullName(student)}
        </h1>
        <p className="flex flex-wrap gap-2">
          <StudentStatusBadge active={student.is_active} />
          <StudentAgeBadge minor={student.is_minor} />
        </p>
        <FamilyLine family={student.family} />
        {canManage ? (
          <div className="flex flex-wrap items-center gap-3">
            <Link
              to={`/students/${student.id}/edit`}
              className={LINK_BUTTON_CLASS}
            >
              {t('students.edit')}
            </Link>
            <Button
              type="button"
              variant={student.is_active ? 'outline' : 'default'}
              onClick={() => {
                setError(null);
                setSaved(false);
                setConfirming(true);
              }}
            >
              {student.is_active
                ? t('students.deactivate.action')
                : t('students.reactivate')}
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
            retryLabel={isConflict ? t('students.conflict.reload') : undefined}
          />
        ) : null}
        <p role="status" className="text-sm">
          {saved ? t('students.saved') : null}
        </p>
      </div>

      <section aria-labelledby={`${id}-school`} className="flex flex-col gap-3">
        <h2 id={`${id}-school`} className="text-lg font-semibold">
          {t('students.sections.school')}
        </h2>
        <dl className="grid grid-cols-[auto_1fr] gap-x-4 gap-y-2 text-sm">
          <dt className="font-medium">{t('students.details.school')}</dt>
          <dd className="min-w-0 break-words">
            {student.school_name ?? notSet}
          </dd>
          <dt className="font-medium">{t('students.details.grade')}</dt>
          <dd className="min-w-0 break-words">
            {student.school_grade ?? notSet}
          </dd>
          <dt className="font-medium">{t('students.details.level')}</dt>
          <dd className="min-w-0 break-words">
            {student.education_level
              ? levelLabel(student.education_level)
              : notSet}
          </dd>
        </dl>
      </section>

      {personal ? (
        <>
          <section
            aria-labelledby={`${id}-personal`}
            className="flex flex-col gap-3"
          >
            <h2 id={`${id}-personal`} className="text-lg font-semibold">
              {t('students.sections.personal')}
            </h2>
            <dl className="grid grid-cols-[auto_1fr] gap-x-4 gap-y-2 text-sm">
              <dt className="font-medium">{t('students.details.birthdate')}</dt>
              <dd>{formatLocalDate(personal.birthdate, 'long')}</dd>
              <dt className="font-medium">{t('students.details.email')}</dt>
              <dd className="min-w-0 break-words">
                {personal.email ?? notSet}
              </dd>
              <dt className="font-medium">{t('students.details.phone')}</dt>
              <dd className="min-w-0 break-words">
                {personal.phone ? (
                  <a href={`tel:${personal.phone}`} className={TEXT_LINK_CLASS}>
                    {personal.phone}
                  </a>
                ) : (
                  notSet
                )}
              </dd>
            </dl>
          </section>
          <section
            aria-labelledby={`${id}-contacts`}
            className="flex flex-col gap-3"
          >
            <h2 id={`${id}-contacts`} className="text-lg font-semibold">
              {t('students.contacts.title')}
            </h2>
            <ol className="flex list-decimal flex-col gap-2 pl-6 text-sm">
              {personal.emergency_contacts.map((contact) => (
                <li key={contact.id} className="break-words">
                  <span className="font-medium">{contact.name}</span>
                  {' · '}
                  {contact.relationship}
                  {' · '}
                  <a href={`tel:${contact.phone}`} className={TEXT_LINK_CLASS}>
                    {contact.phone}
                  </a>
                </li>
              ))}
            </ol>
          </section>
        </>
      ) : null}

      {student.guardians ? (
        <StudentGuardiansSection
          guardians={student.guardians}
          canManage={canManageGuardians}
          onLink={onLinkGuardian}
          onUnlink={onUnlinkGuardian}
          searchGuardians={searchGuardians}
        />
      ) : null}

      <ActiveDialog
        open={confirming}
        active={student.is_active}
        onConfirm={confirm}
        onCancel={() => setConfirming(false)}
      />
    </div>
  );
}

/** Confirms a deactivation or a reactivation; focus returns to the button. */
function ActiveDialog({
  open,
  active,
  onConfirm,
  onCancel,
}: {
  open: boolean;
  /** The student's current state. */
  active: boolean;
  onConfirm: () => Promise<void>;
  onCancel: () => void;
}) {
  const { t } = useTranslation();
  const sending = useRef(false);
  const [busy, setBusy] = useState(false);
  const returnFocus = useRef<Element | null>(null);

  async function confirm() {
    if (sending.current) {
      return;
    }
    sending.current = true;
    setBusy(true);
    try {
      await onConfirm();
    } finally {
      sending.current = false;
      setBusy(false);
    }
  }

  return (
    <Dialog
      open={open}
      onOpenChange={(next) => {
        if (!next && !sending.current) {
          onCancel();
        }
      }}
    >
      {open ? (
        <DialogContent
          onOpenAutoFocus={() => {
            returnFocus.current = document.activeElement;
          }}
          onCloseAutoFocus={(event) => {
            event.preventDefault();
            const target = returnFocus.current;
            if (target instanceof HTMLElement && target.isConnected) {
              target.focus();
            }
          }}
          onInteractOutside={(event) => event.preventDefault()}
        >
          <DialogHeader>
            <DialogTitle>
              {active
                ? t('students.deactivate.title')
                : t('students.reactivateTitle')}
            </DialogTitle>
            <DialogDescription>
              {active
                ? t('students.deactivate.body')
                : t('students.reactivateBody')}
            </DialogDescription>
          </DialogHeader>
          <DialogFooter>
            <Button
              type="button"
              variant="outline"
              disabled={busy}
              onClick={onCancel}
            >
              {t('students.cancel')}
            </Button>
            <Button
              type="button"
              variant={active ? 'destructive' : 'default'}
              disabled={busy}
              onClick={() => void confirm()}
            >
              {busy ? (
                <Spinner label={t('students.saving')} showLabel />
              ) : active ? (
                t('students.deactivate.action')
              ) : (
                t('students.reactivate')
              )}
            </Button>
          </DialogFooter>
        </DialogContent>
      ) : null}
    </Dialog>
  );
}
