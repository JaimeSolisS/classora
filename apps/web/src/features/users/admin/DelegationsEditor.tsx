import { useId, useRef, useState, type FormEvent } from 'react';
import { useTranslation } from 'react-i18next';
import { ApiError } from '@/app/api';
import { ErrorAlert } from '@/components/feedback/ErrorAlert';
import { Spinner } from '@/components/feedback/Spinner';
import { Button } from '@/components/ui/button';
import { Checkbox } from '@/components/ui/checkbox';
import {
  DELEGABLE_PERMISSIONS,
  type DelegablePermission,
  type UserDetail,
} from './api';
import { delegationKey, orderedDelegations, setsDiffer } from './rules';

// The "Delegated permissions" section of the user detail page (#170 G2,
// G3), rendered only for a user who holds `administrator`. With
// `allowed_actions.manage_delegations` (an Owner): four checkboxes, each with
// its label and a one-line description, and "Save delegated permissions",
// sent through the page's `onSave` (the #28 password dialog, retried once).
// Otherwise the current delegations as text. A change never signs the
// Administrator out. Without any API call: stories pass stubs.

interface DelegationsEditorProps {
  user: UserDetail;
  /** Sends the permissions; rejects with the error to show. */
  onSave: (permissions: DelegablePermission[]) => Promise<void>;
  /** Refetches the detail after a version conflict. */
  onReload: () => Promise<void>;
}

export function DelegationsEditor({
  user,
  onSave,
  onReload,
}: DelegationsEditorProps) {
  if (!user.allowed_actions.manage_delegations) {
    return <DelegationsReadOnly user={user} />;
  }
  return <DelegationsForm user={user} onSave={onSave} onReload={onReload} />;
}

function DelegationsForm({ user, onSave, onReload }: DelegationsEditorProps) {
  const { t } = useTranslation();
  const id = useId();
  const titleId = `${id}-title`;
  const noteId = `${id}-note`;
  const [selected, setSelected] = useState<DelegablePermission[]>(
    user.delegations,
  );
  const [error, setError] = useState<unknown>(null);
  const [saved, setSaved] = useState(false);
  const [sending, setSending] = useState(false);
  const inFlight = useRef(false);

  // A new version from the server (a save, a reload): show its delegations.
  const [loadedVersion, setLoadedVersion] = useState(user.version);
  if (loadedVersion !== user.version) {
    setLoadedVersion(user.version);
    setSelected(user.delegations);
  }

  const changed = setsDiffer(selected, user.delegations);

  function toggle(code: DelegablePermission, checked: boolean) {
    setSaved(false);
    setSelected((current) =>
      checked
        ? orderedDelegations([...current, code])
        : current.filter((c) => c !== code),
    );
  }

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!changed || inFlight.current) {
      return;
    }
    inFlight.current = true;
    setSending(true);
    setError(null);
    setSaved(false);
    try {
      await onSave(orderedDelegations(selected));
      setSaved(true);
    } catch (caught) {
      setError(caught);
    } finally {
      inFlight.current = false;
      setSending(false);
    }
  }

  async function reload() {
    setError(null);
    setSaved(false);
    await onReload();
  }

  const isConflict =
    error instanceof ApiError && error.code === 'version_conflict';

  return (
    <section
      aria-labelledby={titleId}
      className="flex w-full max-w-md flex-col gap-4"
    >
      <h2 id={titleId} className="text-lg font-semibold">
        {t('users.delegations.title')}
      </h2>
      {error ? (
        <ErrorAlert
          error={error}
          onRetry={isConflict ? () => void reload() : undefined}
          retryLabel={isConflict ? t('users.conflict.reload') : undefined}
        />
      ) : null}
      <form
        method="post"
        noValidate
        onSubmit={(event) => void submit(event)}
        className="flex flex-col gap-4"
      >
        <fieldset className="flex flex-col gap-3">
          <legend className="sr-only">{t('users.delegations.title')}</legend>
          {DELEGABLE_PERMISSIONS.map((code) => {
            const boxId = `${id}-${code}`;
            const key = delegationKey(code);
            return (
              <div key={code} className="flex items-start gap-3">
                <Checkbox
                  id={boxId}
                  name={`permissions.${code}`}
                  className="mt-0.5"
                  checked={selected.includes(code)}
                  disabled={sending}
                  aria-describedby={`${boxId}-description`}
                  onCheckedChange={(checked) => toggle(code, checked === true)}
                />
                <div className="flex min-w-0 flex-col gap-0.5">
                  <label htmlFor={boxId} className="text-sm font-medium">
                    {t(`${key}.label`)}
                  </label>
                  <p
                    id={`${boxId}-description`}
                    className="text-sm text-muted-foreground"
                  >
                    {t(`${key}.description`)}
                  </p>
                </div>
              </div>
            );
          })}
        </fieldset>
        <div className="flex flex-wrap items-center gap-3">
          <Button
            type="submit"
            disabled={!changed || sending}
            aria-describedby={noteId}
          >
            {sending ? (
              <Spinner label={t('users.delegations.saving')} showLabel />
            ) : (
              t('users.delegations.save')
            )}
          </Button>
          <p id={noteId} className="text-sm text-muted-foreground">
            {t('users.roles.reauthNote')}
          </p>
        </div>
        <p role="status" className="text-sm">
          {saved ? t('users.delegations.saved') : null}
        </p>
      </form>
    </section>
  );
}

/** The delegations as text (an Administrator viewing an Administrator). */
function DelegationsReadOnly({ user }: { user: UserDetail }) {
  const { t } = useTranslation();
  const id = useId();
  return (
    <section aria-labelledby={`${id}-title`} className="flex flex-col gap-4">
      <h2 id={`${id}-title`} className="text-lg font-semibold">
        {t('users.delegations.title')}
      </h2>
      {user.delegations.length > 0 ? (
        <ul className="flex list-disc flex-col gap-1 pl-5 text-sm">
          {user.delegations.map((code) => (
            <li key={code}>{t(`${delegationKey(code)}.label`)}</li>
          ))}
        </ul>
      ) : (
        <p className="text-sm">{t('users.delegations.none')}</p>
      )}
    </section>
  );
}
