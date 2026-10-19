import { useQuery } from '@tanstack/react-query';
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
import { FieldError } from '@/features/auth/FieldError';
import { useSession } from '@/features/auth/useSession';
import { ConfirmDialog } from '@/features/guardians/ConfirmDialog';
import { SELECT_CLASS } from '@/features/students/styles';
import { useFormatters } from '@/lib/formatters';
import { cn } from '@/lib/utils';
import {
  consentsKey,
  fetchConsents,
  recordConsent,
  revokeConsent,
  type ConsentCreate,
  type ConsentGuardianOption,
  type ConsentMethod,
  type ConsentOverview,
  type ConsentRecordItem,
  type ConsentRevocation,
  type ConsentScope,
} from './api';
import { consentMode, type ConsentMode } from './rules';

// The Consents section of the student detail (#35 I2), shown when the viewer
// holds consent.record and GET …/consents answers 200 (hidden on 404). A
// summary (minor or adult, and the login state), the records the viewer may
// see (scope, guardian, method, notice version, dates and an Active/Revoked
// badge as text), and the actions of the viewer's role:
// - staff (Owner, Administrator): "Record consent" opens a dialog (scope,
//   guardian, Paper / In person, the notice version read-only); each record
//   they may revoke has "Revoke";
// - a guardian of a minor: "Allow {{name}} to have a login" behind a
//   confirmation naming the notice version, then "You allowed a login on
//   {{date}}" and "Withdraw consent";
// - an adult student: each guardian with "Share my academic records" or
//   "Stop sharing".
// Revoking asks for confirmation and warns when it suspends the login. After
// any change the overview and the detail refetch (`onChanged`) and a polite
// status announces the result.

const STAFF_METHODS: readonly ConsentMethod[] = ['paper', 'in_person'];

interface ConsentSectionProps {
  studentId: string;
  /** The student's display name (confirmations and warnings). */
  studentName: string;
  /** Called after a consent is recorded or revoked (refetch the detail). */
  onChanged: () => Promise<void>;
}

/** The section with its own query; renders nothing on 404 or while loading. */
export function ConsentSection({
  studentId,
  studentName,
  onChanged,
}: ConsentSectionProps) {
  const session = useSession();
  const roles = session.status === 'signedIn' ? session.user.roles : [];
  const mode = consentMode(roles);
  const overview = useQuery({
    queryKey: consentsKey(studentId),
    queryFn: ({ signal }) => fetchConsents(studentId, signal),
    enabled: mode !== null,
  });

  if (mode === null || !overview.data) {
    if (
      overview.isError &&
      !(overview.error instanceof ApiError && overview.error.status === 404)
    ) {
      return (
        <ErrorAlert
          error={overview.error}
          onRetry={() => void overview.refetch()}
        />
      );
    }
    return null;
  }

  async function changed() {
    await Promise.all([overview.refetch(), onChanged()]);
  }

  return (
    <ConsentSectionView
      overview={overview.data}
      mode={mode}
      studentName={studentName}
      onRecord={async (body) => {
        await recordConsent(studentId, body);
        await changed();
      }}
      onRevoke={async (consentId) => {
        const result = await revokeConsent(studentId, consentId);
        await changed();
        return result;
      }}
    />
  );
}

interface ConsentSectionViewProps {
  overview: ConsentOverview;
  mode: ConsentMode;
  studentName: string;
  /** Records; rejects with the error to show. */
  onRecord: (body: ConsentCreate) => Promise<void>;
  /** Revokes; rejects with the error to show. */
  onRevoke: (consentId: string) => Promise<ConsentRevocation>;
}

function guardianName(item: { given_names: string; family_names: string }) {
  return `${item.given_names} ${item.family_names}`;
}

function recordGuardianName(record: ConsentRecordItem) {
  return `${record.guardian_given_names} ${record.guardian_family_names}`;
}

function isActive(record: ConsentRecordItem) {
  return record.revoked_at === null;
}

/** The section's content, without any query (stories use it). */
export function ConsentSectionView({
  overview,
  mode,
  studentName,
  onRecord,
  onRevoke,
}: ConsentSectionViewProps) {
  const { t } = useTranslation();
  const { formatInstant } = useFormatters();
  const id = useId();
  const [announcement, setAnnouncement] = useState('');
  const [error, setError] = useState<unknown>(null);
  const [recording, setRecording] = useState(false);
  const [revoking, setRevoking] = useState<ConsentRecordItem | null>(null);
  const [granting, setGranting] = useState<{
    scope: ConsentScope;
    guardian: ConsentGuardianOption;
  } | null>(null);
  const version = overview.current_notice_version;
  const activeGuardians = overview.guardians.filter((g) => g.is_active);

  function start() {
    setError(null);
    setAnnouncement('');
  }

  async function revoke() {
    if (!revoking) {
      return;
    }
    setError(null);
    try {
      const result = await onRevoke(revoking.id);
      setAnnouncement(
        result.student_login_suspended
          ? `${t('consent.revokedStatus')} ${t('consent.suspended')}`
          : t('consent.revokedStatus'),
      );
    } catch (caught) {
      setError(caught);
    } finally {
      setRevoking(null);
    }
  }

  async function grant() {
    if (!granting) {
      return;
    }
    setError(null);
    try {
      await onRecord({
        scope: granting.scope,
        guardian_id: granting.guardian.guardian_id,
        method: 'in_app',
      });
      setAnnouncement(t('consent.recorded'));
    } catch (caught) {
      setError(caught);
    } finally {
      setGranting(null);
    }
  }

  function activeRecord(scope: ConsentScope, guardianId: string) {
    return overview.consents.find(
      (record) =>
        isActive(record) &&
        record.scope === scope &&
        record.guardian_id === guardianId,
    );
  }

  let actions = null;
  if (mode === 'staff' && overview.can_record.length > 0) {
    actions = (
      <Button
        type="button"
        variant="outline"
        onClick={() => {
          start();
          setRecording(true);
        }}
      >
        {t('consent.record')}
      </Button>
    );
  } else if (mode === 'guardian' && overview.is_minor) {
    const own = overview.guardians[0];
    const given = own ? activeRecord('student_login', own.guardian_id) : null;
    if (given) {
      actions = (
        <div className="flex flex-wrap items-center gap-3 text-sm">
          <span>
            {t('consent.loginGiven', {
              date: formatInstant(given.granted_at, 'date'),
            })}
          </span>
          <Button
            type="button"
            variant="outline"
            onClick={() => {
              start();
              setRevoking(given);
            }}
          >
            {t('consent.withdraw')}
          </Button>
        </div>
      );
    } else if (own && overview.can_record.includes('student_login')) {
      actions = (
        <Button
          type="button"
          onClick={() => {
            start();
            setGranting({ scope: 'student_login', guardian: own });
          }}
        >
          {t('consent.allowLogin', { name: studentName })}
        </Button>
      );
    }
  } else if (
    mode === 'student' &&
    overview.can_record.includes('guardian_academic_access')
  ) {
    actions = (
      <ul className="flex flex-col gap-3 text-sm">
        {activeGuardians.map((guardian) => {
          const shared = activeRecord(
            'guardian_academic_access',
            guardian.guardian_id,
          );
          const name = guardianName(guardian);
          return (
            <li
              key={guardian.guardian_id}
              className="flex flex-wrap items-center gap-x-3 gap-y-2 break-words"
            >
              <span className="font-medium">{name}</span>
              <span>{t(`relationships.${guardian.relationship}`)}</span>
              {shared ? (
                <>
                  <span>
                    {t('consent.sharedWith', {
                      date: formatInstant(shared.granted_at, 'date'),
                    })}
                  </span>
                  <Button
                    type="button"
                    variant="outline"
                    size="sm"
                    aria-label={`${t('consent.stopSharing')}: ${name}`}
                    onClick={() => {
                      start();
                      setRevoking(shared);
                    }}
                  >
                    {t('consent.stopSharing')}
                  </Button>
                </>
              ) : (
                <Button
                  type="button"
                  size="sm"
                  aria-label={`${t('consent.share')}: ${name}`}
                  onClick={() => {
                    start();
                    setGranting({
                      scope: 'guardian_academic_access',
                      guardian,
                    });
                  }}
                >
                  {t('consent.share')}
                </Button>
              )}
            </li>
          );
        })}
      </ul>
    );
  }

  const grantName = granting ? guardianName(granting.guardian) : '';

  return (
    <section aria-labelledby={`${id}-consent`} className="flex flex-col gap-3">
      <h2 id={`${id}-consent`} className="text-lg font-semibold">
        {t('consent.title')}
      </h2>
      <p className="text-sm">
        {overview.is_minor
          ? t('consent.summary.minor')
          : t('consent.summary.adult')}{' '}
        {overview.login_state
          ? t('consent.loginState', {
              state: t(`userStates.${overview.login_state}`),
            })
          : t('consent.noLogin')}
      </p>
      <p role="status" className="text-sm">
        {announcement}
      </p>
      {error ? <ErrorAlert error={error} /> : null}
      {actions}
      {overview.consents.length === 0 ? (
        <p className="text-sm">{t('consent.empty')}</p>
      ) : (
        <ul className="flex flex-col gap-3 text-sm">
          {overview.consents.map((record) => (
            <ConsentRow
              key={record.id}
              record={record}
              canRevoke={mode === 'staff' && record.can_revoke}
              onRevoke={() => {
                start();
                setRevoking(record);
              }}
            />
          ))}
        </ul>
      )}
      <RecordConsentDialog
        open={recording}
        overview={overview}
        guardians={activeGuardians}
        onCancel={() => setRecording(false)}
        onRecord={async (body) => {
          await onRecord(body);
          setRecording(false);
          setAnnouncement(t('consent.recorded'));
        }}
      />
      <ConfirmDialog
        open={granting !== null}
        title={
          granting?.scope === 'student_login'
            ? t('consent.allowLogin', { name: studentName })
            : t('consent.share')
        }
        body={
          granting?.scope === 'student_login'
            ? t('consent.allowLoginConfirm', { name: studentName, version })
            : t('consent.shareConfirm', { name: grantName, version })
        }
        confirmLabel={t('consent.confirm')}
        onConfirm={grant}
        onCancel={() => setGranting(null)}
      />
      <ConfirmDialog
        open={revoking !== null}
        title={t('consent.revokeTitle')}
        body={
          revoking ? (
            <>
              {t('consent.revokeBody', {
                scope: t(`consent.scope.${revoking.scope}`),
                name: recordGuardianName(revoking),
              })}
              {revoking.suspends_login_on_revoke ? (
                <strong className="mt-2 block font-semibold">
                  {t('consent.revokeWarning', { name: studentName })}
                </strong>
              ) : null}
            </>
          ) : (
            ''
          )
        }
        confirmLabel={
          mode === 'guardian' && revoking?.scope === 'student_login'
            ? t('consent.withdraw')
            : mode === 'student'
              ? t('consent.stopSharing')
              : t('consent.revoke')
        }
        destructive
        onConfirm={revoke}
        onCancel={() => setRevoking(null)}
      />
    </section>
  );
}

function ConsentRow({
  record,
  canRevoke,
  onRevoke,
}: {
  record: ConsentRecordItem;
  canRevoke: boolean;
  onRevoke: () => void;
}) {
  const { t } = useTranslation();
  const { formatInstant } = useFormatters();
  const scope = t(`consent.scope.${record.scope}`);
  const name = recordGuardianName(record);
  let status = t('consent.active');
  if (record.revoked_at !== null) {
    status =
      record.revocation_cause === 'guardian_unlinked'
        ? t('consent.unlinked')
        : t('consent.revoked', {
            date: formatInstant(record.revoked_at, 'date'),
          });
  }
  return (
    <li className="flex flex-col gap-1 rounded-md border border-border p-3 break-words">
      <div className="flex flex-wrap items-center gap-x-3 gap-y-2">
        <span className="font-medium">{scope}</span>
        <span
          data-slot="consent-status"
          className={cn(
            'inline-flex items-center rounded-md border px-2 py-0.5 text-xs font-medium whitespace-nowrap text-foreground',
            record.revoked_at === null
              ? 'border-border bg-background'
              : 'border-dashed bg-muted',
          )}
        >
          {status}
        </span>
        {canRevoke ? (
          <Button
            type="button"
            variant="outline"
            size="sm"
            aria-label={t('consent.revokeNamed', { scope, name })}
            onClick={onRevoke}
          >
            {t('consent.revoke')}
          </Button>
        ) : null}
      </div>
      <span>{name}</span>
      <span>
        {t(`consent.method.${record.method}`)}
        {' · '}
        {t('consent.noticeVersion', { version: record.notice_version })}
        {' · '}
        {t('consent.given', {
          date: formatInstant(record.granted_at, 'date'),
        })}
      </span>
    </li>
  );
}

type FieldName = 'scope' | 'guardian' | 'method';

const FIELD_OF_CODE: Readonly<Record<string, FieldName>> = {
  consent_not_applicable: 'scope',
  consent_guardian_invalid: 'guardian',
  consent_method_invalid: 'method',
};

interface RecordConsentDialogProps {
  open: boolean;
  overview: ConsentOverview;
  /** The active linked guardians the staff member may name. */
  guardians: readonly ConsentGuardianOption[];
  /** Records; rejects with the error to show inside the dialog. */
  onRecord: (body: ConsentCreate) => Promise<void>;
  onCancel: () => void;
}

/** Staff record a paper or in-person consent. */
export function RecordConsentDialog({
  open,
  overview,
  guardians,
  onRecord,
  onCancel,
}: RecordConsentDialogProps) {
  const { t } = useTranslation();
  const { t: tErrors } = useTranslation('errors');
  const id = useId();
  const returnFocus = useRef<Element | null>(null);
  const sending = useRef(false);
  const [busy, setBusy] = useState(false);
  const [scope, setScope] = useState<ConsentScope | ''>('');
  const [guardianId, setGuardianId] = useState('');
  const [method, setMethod] = useState<ConsentMethod | ''>('');
  const [missing, setMissing] = useState<Record<FieldName, boolean>>({
    scope: false,
    guardian: false,
    method: false,
  });
  const [error, setError] = useState<unknown>(null);
  const offered = overview.can_record;
  const single = offered.length === 1 ? offered[0] : undefined;
  const chosenScope: ConsentScope | '' = scope || single || '';

  function reset() {
    setScope('');
    setGuardianId('');
    setMethod('');
    setMissing({ scope: false, guardian: false, method: false });
    setError(null);
  }

  const serverField =
    error instanceof ApiError ? FIELD_OF_CODE[error.code] : undefined;
  function message(field: FieldName, fallback: string) {
    if (serverField === field && error instanceof ApiError) {
      return tErrors(
        error.code as
          | 'consent_not_applicable'
          | 'consent_guardian_invalid'
          | 'consent_method_invalid',
      );
    }
    return missing[field] ? fallback : undefined;
  }

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (sending.current) {
      return;
    }
    const nextMissing = {
      scope: chosenScope === '',
      guardian: guardianId === '',
      method: method === '',
    };
    setMissing(nextMissing);
    if (chosenScope === '' || guardianId === '' || method === '') {
      return;
    }
    sending.current = true;
    setBusy(true);
    setError(null);
    try {
      await onRecord({ scope: chosenScope, guardian_id: guardianId, method });
      reset();
    } catch (caught) {
      setError(caught);
    } finally {
      sending.current = false;
      setBusy(false);
    }
  }

  const ids = {
    scope: `${id}-scope`,
    scopeError: `${id}-scope-error`,
    guardian: `${id}-guardian`,
    guardianError: `${id}-guardian-error`,
    method: `${id}-method`,
    methodError: `${id}-method-error`,
  };
  const scopeMessage = message('scope', t('consent.chooseScope'));
  const guardianMessage = message('guardian', t('consent.chooseGuardian'));
  const methodMessage = message('method', t('consent.chooseMethod'));

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
            <DialogTitle>{t('consent.record')}</DialogTitle>
            <DialogDescription>
              {t('consent.noticeVersion', {
                version: overview.current_notice_version,
              })}
            </DialogDescription>
          </DialogHeader>
          <form
            noValidate
            onSubmit={(event) => void submit(event)}
            className="flex flex-col gap-4"
          >
            {error && !serverField ? <ErrorAlert error={error} /> : null}
            <RadioGroup
              legend={t('consent.scopeLegend')}
              name={ids.scope}
              value={chosenScope}
              options={offered.map((code) => ({
                value: code,
                label: t(`consent.scope.${code}`),
              }))}
              errorId={ids.scopeError}
              errorMessage={scopeMessage}
              onChange={(value) => {
                setScope(value as ConsentScope);
                setMissing((m) => ({ ...m, scope: false }));
              }}
            />
            <div className="flex flex-col gap-2">
              <label htmlFor={ids.guardian} className="text-sm font-medium">
                {t('consent.guardian')}
              </label>
              <select
                id={ids.guardian}
                className={SELECT_CLASS}
                value={guardianId}
                aria-invalid={guardianMessage ? true : undefined}
                aria-describedby={
                  guardianMessage ? ids.guardianError : undefined
                }
                onChange={(event) => {
                  setGuardianId(event.target.value);
                  setMissing((m) => ({ ...m, guardian: false }));
                }}
              >
                <option value="">{t('consent.chooseGuardian')}</option>
                {guardians.map((guardian) => (
                  <option
                    key={guardian.guardian_id}
                    value={guardian.guardian_id}
                  >
                    {guardianName(guardian)} ·{' '}
                    {t(`relationships.${guardian.relationship}`)}
                  </option>
                ))}
              </select>
              <FieldError id={ids.guardianError} message={guardianMessage} />
            </div>
            <RadioGroup
              legend={t('consent.methodLegend')}
              name={ids.method}
              value={method}
              options={STAFF_METHODS.map((code) => ({
                value: code,
                label: t(`consent.method.${code}`),
              }))}
              errorId={ids.methodError}
              errorMessage={methodMessage}
              onChange={(value) => {
                setMethod(value as ConsentMethod);
                setMissing((m) => ({ ...m, method: false }));
              }}
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
                  t('consent.recordAction')
                )}
              </Button>
            </DialogFooter>
          </form>
        </DialogContent>
      ) : null}
    </Dialog>
  );
}

/** Native radios grouped with a legend (J2); the error is described. */
function RadioGroup({
  legend,
  name,
  value,
  options,
  errorId,
  errorMessage,
  onChange,
}: {
  legend: string;
  name: string;
  value: string;
  options: readonly { value: string; label: string }[];
  errorId: string;
  errorMessage: string | undefined;
  onChange: (value: string) => void;
}) {
  return (
    <fieldset
      className="flex flex-col gap-2"
      aria-describedby={errorMessage ? errorId : undefined}
    >
      <legend className="mb-1 text-sm font-medium">{legend}</legend>
      {options.map((option) => (
        <label
          key={option.value}
          className="flex min-h-9 cursor-pointer items-center gap-3 rounded-md border border-border px-3 py-1.5 text-sm has-[:focus-visible]:ring-3 has-[:focus-visible]:ring-ring"
        >
          <input
            type="radio"
            name={name}
            value={option.value}
            checked={value === option.value}
            className="size-4 shrink-0 accent-primary"
            onChange={() => onChange(option.value)}
          />
          <span>{option.label}</span>
        </label>
      ))}
      <FieldError id={errorId} message={errorMessage} />
    </fieldset>
  );
}
