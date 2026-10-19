import { useId, useRef, useState, type FormEvent, type RefObject } from 'react';
import { useTranslation } from 'react-i18next';
import { ApiError } from '@/app/api';
import { ErrorAlert } from '@/components/feedback/ErrorAlert';
import { Spinner } from '@/components/feedback/Spinner';
import { Button } from '@/components/ui/button';
import { Checkbox } from '@/components/ui/checkbox';
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog';
import type { RoleCode } from '@/features/auth/session';
import { z } from '@/lib/forms';
import { ROLE_ORDER, roleNameKey } from '../roles';
import type { UserDetail } from './api';
import { orderedRoles, setsDiffer } from './rules';

// The Roles section of the user detail page (#170 G1, G3, G4): a fieldset of
// the seven roles. A role outside `allowed_actions.assignable_roles` is
// disabled and says why. "Save roles" is enabled only when the selection
// changed; the page's `onSave` sends the complete set with the loaded
// version through apiCall (the #28 password dialog, retried once). On the
// caller's own record a warning says that saving signs them out, and Save
// asks for a confirmation first. Errors show in this section's alert; a
// version conflict adds "Reload". Without any API call: stories pass stubs.

const rolesSchema = z
  .array(z.enum(ROLE_ORDER as [RoleCode, ...RoleCode[]]))
  .min(1, 'users.roles.required');

interface RolesEditorProps {
  user: UserDetail;
  /** The signed-in user's own record (G4). */
  ownRecord: boolean;
  /** Sends the roles; rejects with the error to show. */
  onSave: (roles: RoleCode[]) => Promise<void>;
  /** Refetches the detail after a version conflict. */
  onReload: () => Promise<void>;
}

export function RolesEditor({
  user,
  ownRecord,
  onSave,
  onReload,
}: RolesEditorProps) {
  const { t } = useTranslation();
  const id = useId();
  const ids = {
    title: `${id}-title`,
    locked: `${id}-locked`,
    required: `${id}-required`,
    warning: `${id}-warning`,
    note: `${id}-note`,
  };
  const [selected, setSelected] = useState<RoleCode[]>(user.roles);
  const [invalid, setInvalid] = useState(false);
  const [error, setError] = useState<unknown>(null);
  const [saved, setSaved] = useState(false);
  const [sending, setSending] = useState(false);
  const [confirming, setConfirming] = useState(false);
  const inFlight = useRef(false);
  const saveRef = useRef<HTMLButtonElement>(null);
  const firstBoxRef = useRef<HTMLButtonElement>(null);

  // A new version from the server (a save, a reload): show its roles.
  const [loadedVersion, setLoadedVersion] = useState(user.version);
  if (loadedVersion !== user.version) {
    setLoadedVersion(user.version);
    setSelected(user.roles);
    setInvalid(false);
  }

  const assignable = new Set(user.allowed_actions.assignable_roles);
  const changed = setsDiffer(selected, user.roles);
  const anyLocked = ROLE_ORDER.some((role) => !assignable.has(role));

  function toggle(role: RoleCode, checked: boolean) {
    setSaved(false);
    setInvalid(false);
    setSelected((current) =>
      checked
        ? orderedRoles([...current, role])
        : current.filter((r) => r !== role),
    );
  }

  async function send() {
    if (inFlight.current) {
      return;
    }
    inFlight.current = true;
    setSending(true);
    setError(null);
    setSaved(false);
    try {
      await onSave(orderedRoles(selected));
      setSaved(true);
    } catch (caught) {
      setError(caught);
    } finally {
      inFlight.current = false;
      setSending(false);
      setConfirming(false);
    }
  }

  function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!changed || inFlight.current) {
      return;
    }
    if (!rolesSchema.safeParse(selected).success) {
      setInvalid(true);
      firstBoxRef.current?.focus();
      return;
    }
    if (ownRecord) {
      setError(null);
      setConfirming(true);
      return;
    }
    void send();
  }

  async function reload() {
    setError(null);
    setSaved(false);
    await onReload();
  }

  const isConflict =
    error instanceof ApiError && error.code === 'version_conflict';
  const describedBy = (role: RoleCode) =>
    [assignable.has(role) ? null : ids.locked, invalid ? ids.required : null]
      .filter(Boolean)
      .join(' ') || undefined;
  const firstEnabled = ROLE_ORDER.find((role) => assignable.has(role));

  return (
    <section
      aria-labelledby={ids.title}
      className="flex w-full max-w-md flex-col gap-4"
    >
      <h2 id={ids.title} className="text-lg font-semibold">
        {t('users.roles.title')}
      </h2>
      {error ? (
        <ErrorAlert
          error={error}
          onRetry={isConflict ? () => void reload() : undefined}
          retryLabel={isConflict ? t('users.conflict.reload') : undefined}
        />
      ) : null}
      {ownRecord ? (
        <p id={ids.warning} className="text-sm font-medium">
          {t('users.roles.selfWarning')}
        </p>
      ) : null}
      <form
        method="post"
        noValidate
        onSubmit={submit}
        className="flex flex-col gap-4"
      >
        <fieldset
          className="flex flex-col gap-3"
          aria-describedby={ownRecord ? ids.warning : undefined}
        >
          <legend className="sr-only">{t('users.roles.title')}</legend>
          {ROLE_ORDER.map((role) => {
            const boxId = `${id}-role-${role}`;
            return (
              <div key={role} className="flex items-center gap-3">
                <Checkbox
                  id={boxId}
                  name={`roles.${role}`}
                  ref={role === firstEnabled ? firstBoxRef : undefined}
                  checked={selected.includes(role)}
                  disabled={!assignable.has(role) || sending}
                  aria-invalid={invalid ? true : undefined}
                  aria-describedby={describedBy(role)}
                  onCheckedChange={(checked) => toggle(role, checked === true)}
                />
                <label htmlFor={boxId} className="text-sm">
                  {t(roleNameKey(role))}
                </label>
              </div>
            );
          })}
        </fieldset>
        {anyLocked ? (
          <p id={ids.locked} className="text-sm text-muted-foreground">
            {t('users.roles.locked')}
          </p>
        ) : null}
        {invalid ? (
          <p id={ids.required} className="text-sm text-destructive">
            {t('users.roles.required')}
          </p>
        ) : null}
        <div className="flex flex-wrap items-center gap-3">
          <Button
            ref={saveRef}
            type="submit"
            disabled={!changed || sending}
            aria-describedby={ids.note}
          >
            {sending && !confirming ? (
              <Spinner label={t('users.roles.saving')} showLabel />
            ) : (
              t('users.roles.save')
            )}
          </Button>
          <p id={ids.note} className="text-sm text-muted-foreground">
            {t('users.roles.reauthNote')}
          </p>
        </div>
        <p role="status" className="text-sm">
          {saved ? t('users.roles.saved') : null}
        </p>
      </form>
      <SelfRolesDialog
        open={confirming}
        sending={sending}
        onConfirm={() => void send()}
        onCancel={() => setConfirming(false)}
        returnFocus={saveRef}
      />
    </section>
  );
}

/** "Save your roles? You'll be signed out…" (G4). */
function SelfRolesDialog({
  open,
  sending,
  onConfirm,
  onCancel,
  returnFocus,
}: {
  open: boolean;
  sending: boolean;
  onConfirm: () => void;
  onCancel: () => void;
  returnFocus: RefObject<HTMLButtonElement | null>;
}) {
  const { t } = useTranslation();
  return (
    <Dialog
      open={open}
      onOpenChange={(next) => {
        if (!next && !sending) {
          onCancel();
        }
      }}
    >
      {open ? (
        <DialogContent
          onCloseAutoFocus={(event) => {
            event.preventDefault();
            returnFocus.current?.focus();
          }}
          onInteractOutside={(event) => event.preventDefault()}
        >
          <DialogHeader>
            <DialogTitle>{t('users.roles.selfConfirmTitle')}</DialogTitle>
            <DialogDescription>
              {t('users.roles.selfConfirm')}
            </DialogDescription>
          </DialogHeader>
          <DialogFooter>
            <Button
              type="button"
              variant="outline"
              disabled={sending}
              onClick={onCancel}
            >
              {t('users.roles.cancel')}
            </Button>
            <Button type="button" disabled={sending} onClick={onConfirm}>
              {sending ? (
                <Spinner label={t('users.roles.saving')} showLabel />
              ) : (
                t('users.roles.selfConfirmSave')
              )}
            </Button>
          </DialogFooter>
        </DialogContent>
      ) : null}
    </Dialog>
  );
}
