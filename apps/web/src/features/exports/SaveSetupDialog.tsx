import { useId, useRef, useState, type FormEvent } from 'react';
import { useTranslation } from 'react-i18next';
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
import { Input } from '@/components/ui/input';
import { FieldError } from '@/features/auth/FieldError';
import { useReturnFocus } from '@/features/scheduling/series/hooks';
import { SELECT_CLASS } from '@/features/students/styles';
import { cn } from '@/lib/utils';
import {
  SETUP_NAME_MAX,
  SHARED_ROLES,
  setupNameError,
  setupProblem,
  type SharedRole,
} from './setups';

// The name-and-sharing dialog of a saved export setup (M8-08 D3, D4):
// "Guardar configuración" from the form (with "Actualizar «nombre»" when
// the form holds one of the caller's own setups) and "Editar" on a row.
// A name problem goes under the name; the 50-setup limit is an alert in
// the dialog; a version conflict and a server range problem close it and
// are the caller's (`onStale`, `onRange`). Focus returns to the opener.

export interface SetupFields {
  name: string;
  sharedRole: SharedRole | null;
}

interface SaveSetupDialogProps {
  open: boolean;
  title: string;
  description: string;
  initial: SetupFields;
  submitLabel: string;
  onSubmit: (fields: SetupFields) => Promise<void>;
  /** A second action with the same fields ("Actualizar «nombre»"). */
  alternative?: {
    label: string;
    onSubmit: (fields: SetupFields) => Promise<void>;
  };
  /** 409 version_conflict: the dialog closes; the caller reloads. */
  onStale: () => void;
  /** 422 export_range_invalid: the dialog closes; the form shows it. */
  onRange?: (error: unknown) => void;
  onClose: () => void;
}

export function SaveSetupDialog(props: SaveSetupDialogProps) {
  const busy = useRef(false);
  return (
    <Dialog
      open={props.open}
      onOpenChange={(next) => {
        if (!next && !busy.current) {
          props.onClose();
        }
      }}
    >
      {props.open ? <DialogBody {...props} busyRef={busy} /> : null}
    </Dialog>
  );
}

function DialogBody({
  title,
  description,
  initial,
  submitLabel,
  onSubmit,
  alternative,
  onStale,
  onRange,
  onClose,
  busyRef,
}: SaveSetupDialogProps & { busyRef: { current: boolean } }) {
  const { t } = useTranslation();
  const { t: tErrors } = useTranslation('errors');
  const returnFocus = useReturnFocus();
  const id = useId();
  const ids = {
    name: `${id}-name`,
    nameError: `${id}-name-error`,
    role: `${id}-role`,
    roleHint: `${id}-role-hint`,
  };
  const [name, setName] = useState(initial.name);
  const [role, setRole] = useState<SharedRole | ''>(initial.sharedRole ?? '');
  const [nameMessage, setNameMessage] = useState<string | undefined>();
  const [limit, setLimit] = useState<number | null>(null);
  const [failure, setFailure] = useState<unknown>(null);
  const [submitting, setSubmitting] = useState<'main' | 'alternative' | null>(
    null,
  );

  async function send(which: 'main' | 'alternative') {
    if (busyRef.current) {
      return;
    }
    setFailure(null);
    setLimit(null);
    const found = setupNameError(name);
    setNameMessage(found ? t(`exports.setups.${found}`) : undefined);
    if (found) {
      document.getElementById(ids.name)?.focus();
      return;
    }
    const fields = { name: name.trim(), sharedRole: role || null };
    const action = which === 'main' ? onSubmit : alternative!.onSubmit;
    busyRef.current = true;
    setSubmitting(which);
    try {
      await action(fields);
      busyRef.current = false;
      onClose();
      return;
    } catch (error) {
      const problem = setupProblem(error);
      busyRef.current = false;
      setSubmitting(null);
      if (problem.kind === 'name') {
        setNameMessage(
          problem.key === 'nameTaken'
            ? tErrors('report_setup_name_taken')
            : t('exports.setups.nameInvalid'),
        );
        document.getElementById(ids.name)?.focus();
      } else if (problem.kind === 'limit') {
        setLimit(problem.max);
      } else if (problem.kind === 'stale') {
        onClose();
        onStale();
      } else if (problem.kind === 'range' && onRange) {
        onClose();
        onRange(error);
      } else {
        setFailure(error);
      }
    }
  }

  function onFormSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    void send('main');
  }

  return (
    <DialogContent
      className="sm:max-w-lg"
      onOpenAutoFocus={returnFocus.onOpenAutoFocus}
      onCloseAutoFocus={returnFocus.onCloseAutoFocus}
      onInteractOutside={(event) => event.preventDefault()}
    >
      <DialogHeader>
        <DialogTitle>{title}</DialogTitle>
        <DialogDescription>{description}</DialogDescription>
      </DialogHeader>
      {limit !== null ? (
        <p role="alert" className="text-sm text-destructive">
          {t('exports.setups.limitReached', { max: limit })}
        </p>
      ) : null}
      {failure ? <ErrorAlert error={failure} /> : null}
      <form
        method="post"
        noValidate
        onSubmit={onFormSubmit}
        className="flex flex-col gap-4"
      >
        <div className="flex flex-col gap-2">
          <label htmlFor={ids.name} className="text-sm font-medium">
            {t('exports.setups.name')}
          </label>
          <Input
            id={ids.name}
            type="text"
            autoComplete="off"
            className="min-h-11 md:min-h-9"
            maxLength={SETUP_NAME_MAX + 20}
            value={name}
            required
            aria-invalid={nameMessage ? true : undefined}
            aria-describedby={nameMessage ? ids.nameError : undefined}
            onChange={(event) => setName(event.target.value)}
          />
          <FieldError id={ids.nameError} message={nameMessage} />
        </div>
        <div className="flex flex-col gap-2">
          <label htmlFor={ids.role} className="text-sm font-medium">
            {t('exports.setups.shareWith')}
          </label>
          <select
            id={ids.role}
            className={cn(SELECT_CLASS, 'min-h-11 md:min-h-9')}
            value={role}
            aria-describedby={ids.roleHint}
            onChange={(event) => setRole(event.target.value as SharedRole | '')}
          >
            <option value="">{t('exports.setups.onlyMe')}</option>
            {SHARED_ROLES.map((code) => (
              <option key={code} value={code}>
                {t(`roles.${code}`)}
              </option>
            ))}
          </select>
          <p id={ids.roleHint} className="text-sm text-muted-foreground">
            {t('exports.setups.shareHint')}
          </p>
        </div>
        <DialogFooter>
          <Button
            type="button"
            variant="outline"
            className="min-h-11 md:min-h-9"
            disabled={submitting !== null}
            onClick={onClose}
          >
            {t('exports.setups.cancel')}
          </Button>
          {alternative ? (
            <Button
              type="button"
              variant="outline"
              className="min-h-11 md:min-h-9"
              disabled={submitting !== null}
              onClick={() => void send('alternative')}
            >
              {submitting === 'alternative' ? (
                <Spinner label={t('exports.setups.saving')} showLabel />
              ) : (
                alternative.label
              )}
            </Button>
          ) : null}
          <Button
            type="submit"
            className="min-h-11 md:min-h-9"
            disabled={submitting !== null}
          >
            {submitting === 'main' ? (
              <Spinner label={t('exports.setups.saving')} showLabel />
            ) : (
              submitLabel
            )}
          </Button>
        </DialogFooter>
      </form>
    </DialogContent>
  );
}
