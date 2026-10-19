import { useId, useRef, useState, type FormEvent } from 'react';
import { useTranslation } from 'react-i18next';
import { ApiError } from '@/app/api';
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
import {
  CONSENTS_QUERY_KEY,
  searchLoginAccounts,
  type LoginAccountOption,
  type StudentLogin,
} from '@/features/consent/api';
import { ConfirmDialog } from '@/features/guardians/ConfirmDialog';
import { SearchPicker } from '@/features/guardians/SearchPicker';
import { StateBadge } from '@/features/users/admin/StateBadge';

// The Login account section of the student detail (#35 I1), shown when the
// server sent `login` or the viewer holds family.manage. It shows the linked
// account (username, display name, a state badge as text). With
// family.manage: "Link account" (none) or "Change" open a dialog with an
// account picker over GET /users?q= (roles shown, archived accounts
// disabled); "Remove" unlinks behind a confirmation. The account errors and
// guardian_consent_required show inside the dialog. For a minor, help text
// says the login works only after a linked guardian consents.

interface StudentLoginSectionProps {
  login: StudentLogin | null;
  isMinor: boolean;
  /** The caller holds family.manage (UX only; the API decides). */
  canManage: boolean;
  /** Links (an id) or unlinks (null); rejects with the error to show. */
  onChange: (userId: string | null) => Promise<void>;
  /** The account search (stories pass a stub). */
  searchAccounts?: (
    q: string,
    signal: AbortSignal,
  ) => Promise<LoginAccountOption[]>;
}

export function StudentLoginSection({
  login,
  isMinor,
  canManage,
  onChange,
  searchAccounts = searchLoginAccounts,
}: StudentLoginSectionProps) {
  const { t } = useTranslation();
  const id = useId();
  const [announcement, setAnnouncement] = useState('');
  const [picking, setPicking] = useState(false);
  const [removing, setRemoving] = useState(false);
  const [error, setError] = useState<unknown>(null);

  async function remove() {
    setError(null);
    try {
      await onChange(null);
      setAnnouncement(t('students.login.removed'));
    } catch (caught) {
      setError(caught);
    } finally {
      setRemoving(false);
    }
  }

  function start() {
    setError(null);
    setAnnouncement('');
  }

  return (
    <section aria-labelledby={`${id}-login`} className="flex flex-col gap-3">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <h2 id={`${id}-login`} className="text-lg font-semibold">
          {t('students.login.title')}
        </h2>
        {canManage ? (
          <div className="flex flex-wrap gap-2">
            <Button
              type="button"
              variant="outline"
              onClick={() => {
                start();
                setPicking(true);
              }}
            >
              {login ? t('students.login.change') : t('students.login.link')}
            </Button>
            {login ? (
              <Button
                type="button"
                variant="outline"
                onClick={() => {
                  start();
                  setRemoving(true);
                }}
              >
                {t('students.login.remove')}
              </Button>
            ) : null}
          </div>
        ) : null}
      </div>
      {isMinor && canManage ? (
        <p className="text-sm text-muted-foreground">
          {t('students.login.minorHelp')}
        </p>
      ) : null}
      <p role="status" className="text-sm">
        {announcement}
      </p>
      {error ? <ErrorAlert error={error} /> : null}
      {login ? (
        <dl className="grid grid-cols-[auto_1fr] items-center gap-x-4 gap-y-2 text-sm">
          <dt className="font-medium">{t('students.login.username')}</dt>
          <dd className="min-w-0 break-words">{login.username}</dd>
          <dt className="font-medium">{t('students.login.displayName')}</dt>
          <dd className="min-w-0 break-words">{login.display_name}</dd>
          <dt className="font-medium">{t('students.login.state')}</dt>
          <dd>
            <StateBadge state={login.state} />
          </dd>
        </dl>
      ) : (
        <p className="text-sm">{t('students.login.none')}</p>
      )}
      <LinkAccountDialog
        open={picking}
        isMinor={isMinor}
        searchAccounts={searchAccounts}
        onCancel={() => setPicking(false)}
        onLink={async (userId) => {
          await onChange(userId);
          setPicking(false);
          setAnnouncement(t('students.login.linked'));
        }}
      />
      <ConfirmDialog
        open={removing}
        title={t('students.login.removeTitle')}
        body={t('students.login.removeBody', {
          name: login?.username ?? '',
        })}
        confirmLabel={t('students.login.remove')}
        destructive
        onConfirm={remove}
        onCancel={() => setRemoving(false)}
      />
    </section>
  );
}

type AccountFieldCode =
  'student_account_unavailable' | 'student_account_in_use';

const ACCOUNT_FIELD_CODES: ReadonlySet<string> = new Set<AccountFieldCode>([
  'student_account_unavailable',
  'student_account_in_use',
]);

interface LinkAccountDialogProps {
  open: boolean;
  isMinor: boolean;
  searchAccounts: (
    q: string,
    signal: AbortSignal,
  ) => Promise<LoginAccountOption[]>;
  /** Links; rejects with the error to show inside the dialog. */
  onLink: (userId: string) => Promise<void>;
  onCancel: () => void;
}

/** The account dialog: a picker over every account, archived disabled. */
export function LinkAccountDialog({
  open,
  isMinor,
  searchAccounts,
  onLink,
  onCancel,
}: LinkAccountDialogProps) {
  const { t } = useTranslation();
  const { t: tErrors } = useTranslation('errors');
  const returnFocus = useRef<Element | null>(null);
  const sending = useRef(false);
  const [busy, setBusy] = useState(false);
  const [account, setAccount] = useState<LoginAccountOption | null>(null);
  const [missing, setMissing] = useState(false);
  const [error, setError] = useState<unknown>(null);

  function reset() {
    setAccount(null);
    setMissing(false);
    setError(null);
  }

  function label(option: LoginAccountOption) {
    const roles = option.roles.map((role) => t(`roles.${role}`)).join(', ');
    const base = [
      option.username,
      option.display_name,
      t(`userStates.${option.state}`),
      roles,
    ]
      .filter(Boolean)
      .join(' · ');
    return option.state === 'archived'
      ? `${base} ${t('students.login.archived')}`
      : base;
  }

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (sending.current) {
      return;
    }
    if (!account) {
      setMissing(true);
      return;
    }
    sending.current = true;
    setBusy(true);
    setError(null);
    try {
      await onLink(account.id);
      reset();
    } catch (caught) {
      setError(caught);
    } finally {
      sending.current = false;
      setBusy(false);
    }
  }

  // The two account errors belong to the picker; anything else (for example
  // guardian_consent_required or version_conflict) is an alert.
  const accountError =
    error instanceof ApiError && ACCOUNT_FIELD_CODES.has(error.code)
      ? tErrors(error.code as AccountFieldCode)
      : undefined;
  const fieldError =
    accountError ?? (missing ? t('students.login.chooseAccount') : undefined);
  const alert = accountError ? null : error;

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
            <DialogTitle>{t('students.login.linkTitle')}</DialogTitle>
            <DialogDescription>
              {t('students.login.linkBody')}
              {isMinor ? ` ${t('students.login.minorHelp')}` : null}
            </DialogDescription>
          </DialogHeader>
          <form
            noValidate
            onSubmit={(event) => void submit(event)}
            className="flex flex-col gap-4"
          >
            {alert ? <ErrorAlert error={alert} /> : null}
            <SearchPicker<LoginAccountOption>
              label={t('students.login.search')}
              queryKey={[...CONSENTS_QUERY_KEY, 'accounts']}
              search={searchAccounts}
              optionId={(option) => option.id}
              optionLabel={label}
              optionDisabled={(option) => option.state === 'archived'}
              selectedId={account?.id ?? null}
              onSelect={(option) => {
                setAccount(option);
                setMissing(false);
                setError(null);
              }}
              errorMessage={fieldError}
              autoFocus
            />
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
                  t('students.login.linkAction')
                )}
              </Button>
            </DialogFooter>
          </form>
        </DialogContent>
      ) : null}
    </Dialog>
  );
}
