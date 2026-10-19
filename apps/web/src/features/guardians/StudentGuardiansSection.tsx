import { useId, useRef, useState, type FormEvent } from 'react';
import { useTranslation } from 'react-i18next';
import { Link } from 'react-router';
import { ErrorAlert } from '@/components/feedback/ErrorAlert';
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
import { FieldError } from '@/features/auth/FieldError';
import { StudentStatusBadge } from '@/features/students/StudentBadges';
import { SELECT_CLASS, TEXT_LINK_CLASS } from '@/features/students/styles';
import {
  GUARDIANS_QUERY_KEY,
  searchActiveGuardians,
  type GuardianListItem,
  type GuardianRelationship,
  type StudentGuardianCreate,
  type StudentGuardianItem,
} from './api';
import { ConfirmDialog } from './ConfirmDialog';
import { AccountBadge } from './GuardianBadges';
import { fullName, listName, RELATIONSHIPS } from './rules';
import { SearchPicker } from './SearchPicker';

// The Guardians section of the student detail (#34 I4), shown only when the
// server sent `guardians` (staff with guardian.view). Each row: the name (a
// link to the guardian), the relationship, the phone (tel:) and badges for
// an inactive profile and the account. With family.manage: "Link guardian"
// opens a dialog with a guardian picker (active guardians) and a
// relationship select; each row has "Unlink" behind a confirmation. After
// either action the page refetches the student and a polite status says
// "Guardian linked." / "Guardian unlinked.".

interface StudentGuardiansSectionProps {
  guardians: readonly StudentGuardianItem[];
  /** The caller holds family.manage (UX only; the API decides). */
  canManage: boolean;
  /** Links a guardian; rejects with the error to show in the dialog. */
  onLink: (body: StudentGuardianCreate) => Promise<void>;
  /** Unlinks a guardian; rejects with the error to show. */
  onUnlink: (guardianId: string) => Promise<void>;
  /** The guardian search (stories pass a stub). */
  searchGuardians?: (
    q: string,
    signal: AbortSignal,
  ) => Promise<GuardianListItem[]>;
}

export function StudentGuardiansSection({
  guardians,
  canManage,
  onLink,
  onUnlink,
  searchGuardians = searchActiveGuardians,
}: StudentGuardiansSectionProps) {
  const { t } = useTranslation();
  const id = useId();
  const [announcement, setAnnouncement] = useState('');
  const [linking, setLinking] = useState(false);
  const [unlinking, setUnlinking] = useState<StudentGuardianItem | null>(null);
  const [error, setError] = useState<unknown>(null);

  async function unlink() {
    if (!unlinking) {
      return;
    }
    setError(null);
    try {
      await onUnlink(unlinking.guardian_id);
      setAnnouncement(t('students.guardians.unlinked'));
    } catch (caught) {
      setError(caught);
    } finally {
      setUnlinking(null);
    }
  }

  return (
    <section
      aria-labelledby={`${id}-guardians`}
      className="flex flex-col gap-3"
    >
      <div className="flex flex-wrap items-center justify-between gap-3">
        <h2 id={`${id}-guardians`} className="text-lg font-semibold">
          {t('students.guardians.title')}
        </h2>
        {canManage ? (
          <Button
            type="button"
            variant="outline"
            onClick={() => {
              setError(null);
              setAnnouncement('');
              setLinking(true);
            }}
          >
            {t('students.guardians.link')}
          </Button>
        ) : null}
      </div>
      <p role="status" className="text-sm">
        {announcement}
      </p>
      {error ? <ErrorAlert error={error} /> : null}
      {guardians.length === 0 ? (
        <p className="text-sm">{t('students.guardians.empty')}</p>
      ) : (
        <ul className="flex flex-col gap-3 text-sm">
          {guardians.map((guardian) => (
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
              <span>{t(`relationships.${guardian.relationship}`)}</span>
              {guardian.phone ? (
                <a href={`tel:${guardian.phone}`} className={TEXT_LINK_CLASS}>
                  {guardian.phone}
                </a>
              ) : null}
              <AccountBadge hasAccount={guardian.has_account} />
              {guardian.is_active ? null : (
                <StudentStatusBadge active={false} />
              )}
              {canManage ? (
                <Button
                  type="button"
                  variant="outline"
                  size="sm"
                  aria-label={t('students.guardians.unlinkNamed', {
                    name: fullName(guardian),
                  })}
                  onClick={() => {
                    setError(null);
                    setAnnouncement('');
                    setUnlinking(guardian);
                  }}
                >
                  {t('students.guardians.unlink')}
                </Button>
              ) : null}
            </li>
          ))}
        </ul>
      )}
      <LinkGuardianDialog
        open={linking}
        searchGuardians={searchGuardians}
        onCancel={() => setLinking(false)}
        onLink={async (body) => {
          await onLink(body);
          setLinking(false);
          setAnnouncement(t('students.guardians.linked'));
        }}
      />
      <ConfirmDialog
        open={unlinking !== null}
        title={t('students.guardians.unlinkTitle')}
        body={t('students.guardians.unlinkBody', {
          name: unlinking ? fullName(unlinking) : '',
        })}
        confirmLabel={t('students.guardians.unlink')}
        destructive
        onConfirm={unlink}
        onCancel={() => setUnlinking(null)}
      />
    </section>
  );
}

interface LinkGuardianDialogProps {
  open: boolean;
  searchGuardians: (
    q: string,
    signal: AbortSignal,
  ) => Promise<GuardianListItem[]>;
  /** Links; rejects with the error to show inside the dialog. */
  onLink: (body: StudentGuardianCreate) => Promise<void>;
  onCancel: () => void;
}

/** The link dialog: a guardian picker and a relationship select. */
export function LinkGuardianDialog({
  open,
  searchGuardians,
  onLink,
  onCancel,
}: LinkGuardianDialogProps) {
  const { t } = useTranslation();
  const id = useId();
  const returnFocus = useRef<Element | null>(null);
  const sending = useRef(false);
  const [busy, setBusy] = useState(false);
  const [guardian, setGuardian] = useState<GuardianListItem | null>(null);
  const [relationship, setRelationship] = useState<GuardianRelationship | ''>(
    '',
  );
  const [error, setError] = useState<unknown>(null);
  const [missing, setMissing] = useState<{
    guardian: boolean;
    relationship: boolean;
  }>({ guardian: false, relationship: false });

  function reset() {
    setGuardian(null);
    setRelationship('');
    setError(null);
    setMissing({ guardian: false, relationship: false });
  }

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (sending.current) {
      return;
    }
    const nextMissing = {
      guardian: guardian === null,
      relationship: relationship === '',
    };
    setMissing(nextMissing);
    if (!guardian || !relationship) {
      return;
    }
    sending.current = true;
    setBusy(true);
    setError(null);
    try {
      await onLink({ guardian_id: guardian.id, relationship });
      reset();
    } catch (caught) {
      setError(caught);
    } finally {
      sending.current = false;
      setBusy(false);
    }
  }

  const relationshipId = `${id}-relationship`;
  const relationshipErrorId = `${relationshipId}-error`;

  return (
    <Dialog
      open={open}
      onOpenChange={(next) => {
        if (!next && !sending.current) {
          reset();
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
            <DialogTitle>{t('students.guardians.linkTitle')}</DialogTitle>
            <DialogDescription>
              {t('students.guardians.linkBody')}
            </DialogDescription>
          </DialogHeader>
          <form
            noValidate
            onSubmit={(event) => void submit(event)}
            className="flex flex-col gap-4"
          >
            {error ? <ErrorAlert error={error} /> : null}
            <SearchPicker<GuardianListItem>
              label={t('students.guardians.guardian')}
              queryKey={[...GUARDIANS_QUERY_KEY, 'active-options']}
              search={searchGuardians}
              optionId={(item) => item.id}
              optionLabel={(item) => listName(item)}
              selectedId={guardian?.id ?? null}
              onSelect={(item) => {
                setGuardian(item);
                setMissing((m) => ({ ...m, guardian: false }));
              }}
              errorMessage={
                missing.guardian
                  ? t('students.guardians.chooseGuardian')
                  : undefined
              }
              autoFocus
            />
            <div className="flex flex-col gap-2">
              <label htmlFor={relationshipId} className="text-sm font-medium">
                {t('students.guardians.relationship')}
              </label>
              <select
                id={relationshipId}
                className={SELECT_CLASS}
                value={relationship}
                aria-invalid={missing.relationship ? true : undefined}
                aria-describedby={
                  missing.relationship ? relationshipErrorId : undefined
                }
                onChange={(event) => {
                  setRelationship(event.target.value as GuardianRelationship);
                  setMissing((m) => ({ ...m, relationship: false }));
                }}
              >
                <option value="">
                  {t('students.guardians.chooseRelationship')}
                </option>
                {RELATIONSHIPS.map((code) => (
                  <option key={code} value={code}>
                    {t(`relationships.${code}`)}
                  </option>
                ))}
              </select>
              <FieldError
                id={relationshipErrorId}
                message={
                  missing.relationship
                    ? t('students.guardians.chooseRelationship')
                    : undefined
                }
              />
            </div>
            <DialogFooter>
              <Button
                type="button"
                variant="outline"
                disabled={busy}
                onClick={() => {
                  reset();
                  onCancel();
                }}
              >
                {t('guardians.cancel')}
              </Button>
              <Button type="submit" disabled={busy}>
                {busy ? (
                  <Spinner label={t('guardians.saving')} showLabel />
                ) : (
                  t('students.guardians.linkAction')
                )}
              </Button>
            </DialogFooter>
          </form>
        </DialogContent>
      ) : null}
    </Dialog>
  );
}
