import { useId } from 'react';
import { useTranslation } from 'react-i18next';
import { ErrorAlert } from '@/components/feedback/ErrorAlert';
import { LoadingState } from '@/components/feedback/LoadingState';
import { Checkbox } from '@/components/ui/checkbox';
import type { NotificationPreferenceItem } from './preferences-api';
import { groupPreferences, type PreferenceRow } from './preferenceTypes';

export type PreferencesState =
  | { status: 'loading' }
  | { status: 'error'; error: unknown; onRetry: () => void }
  | {
      status: 'ready';
      items: readonly NotificationPreferenceItem[];
      /** Types being saved → the value asked for (the switch shows it, disabled). */
      saving: ReadonlyMap<string, boolean>;
      onToggle: (type: string, enabled: boolean) => void;
    };

interface NotificationPreferencesViewProps {
  state: PreferencesState;
  /** A failed save (E2): the switch went back; "Try again" resends it. */
  saveError?: { error: unknown; onRetry: () => void } | null;
  /** The catalogue changed and the reload failed (B5): the data may be stale. */
  refreshError?: {
    error: unknown;
    onRetry: () => void;
    retrying: boolean;
  } | null;
  /** The polite announcement ("Saved.", "The list changed; …"). */
  announcement?: string;
}

// /account/notifications without data (M8-02b B, C): the title, the groups
// with their checkboxes, mandatory types locked with "Required", the
// loading, error and empty states, the save and stale alerts, and the one
// polite live region for saves (stories render it directly).
export function NotificationPreferencesView({
  state,
  saveError,
  refreshError,
  announcement = '',
}: NotificationPreferencesViewProps) {
  const { t, i18n } = useTranslation();

  return (
    <div className="flex max-w-prose min-w-0 flex-col gap-6">
      <div className="flex flex-col gap-2">
        <h1 className="text-2xl font-semibold">
          {t('notifications.preferences.title')}
        </h1>
        <p className="text-sm text-muted-foreground">
          {t('notifications.preferences.intro')}
        </p>
      </div>
      <p role="status" aria-live="polite" className="sr-only">
        {announcement}
      </p>
      {state.status === 'loading' ? (
        <LoadingState rows={6} label={t('notifications.preferences.loading')} />
      ) : state.status === 'error' ? (
        <ErrorAlert error={state.error} onRetry={state.onRetry} />
      ) : (
        <>
          {refreshError ? (
            <ErrorAlert
              error={refreshError.error}
              message={t('notifications.preferences.stale')}
              onRetry={refreshError.retrying ? undefined : refreshError.onRetry}
              retryLabel={t('notifications.preferences.reload')}
            />
          ) : null}
          {saveError ? (
            <ErrorAlert
              error={saveError.error}
              title={t('notifications.preferences.saveFailed')}
              onRetry={saveError.onRetry}
            />
          ) : null}
          {state.items.length === 0 ? (
            <p className="text-sm text-muted-foreground">
              {t('notifications.preferences.empty')}
            </p>
          ) : (
            groupPreferences(state.items, t, i18n).map((group) => (
              <PreferenceGroupSection
                key={group.code}
                heading={t(`notifications.preferences.groups.${group.code}`)}
                rows={group.rows}
                saving={state.saving}
                onToggle={state.onToggle}
              />
            ))
          )}
        </>
      )}
    </div>
  );
}

function PreferenceGroupSection({
  heading,
  rows,
  saving,
  onToggle,
}: {
  heading: string;
  rows: PreferenceRow[];
  saving: ReadonlyMap<string, boolean>;
  onToggle: (type: string, enabled: boolean) => void;
}) {
  const { t } = useTranslation();
  const headingId = useId();
  const anyMandatory = rows.some((row) => row.item.mandatory);
  return (
    <section
      aria-labelledby={headingId}
      data-group
      className="flex flex-col gap-1"
    >
      <h2 id={headingId} className="text-lg font-semibold">
        {heading}
      </h2>
      {anyMandatory ? (
        <p className="text-sm text-muted-foreground">
          {t('notifications.preferences.mandatoryIntro')}
        </p>
      ) : null}
      <ul className="flex flex-col">
        {rows.map((row) => (
          <PreferenceItem
            key={row.item.notification_type}
            row={row}
            saving={saving}
            onToggle={onToggle}
          />
        ))}
      </ul>
    </section>
  );
}

function PreferenceItem({
  row: { item, label },
  saving,
  onToggle,
}: {
  row: PreferenceRow;
  saving: ReadonlyMap<string, boolean>;
  onToggle: (type: string, enabled: boolean) => void;
}) {
  const { t } = useTranslation();
  const id = useId();
  const noteId = useId();
  const type = item.notification_type;
  const pending = saving.get(type);
  const checked = item.mandatory || (pending ?? item.enabled);
  return (
    <li
      data-type={type}
      className="flex min-w-0 items-center gap-3 border-b border-border last:border-b-0"
    >
      <Checkbox
        id={id}
        checked={checked}
        disabled={item.mandatory || pending !== undefined}
        aria-describedby={item.mandatory ? noteId : undefined}
        // A 44 × 44 px pointer target (C1); the label row is 44 px tall too.
        className="after:-inset-3.5"
        onCheckedChange={(value) => onToggle(type, value === true)}
      />
      <div className="flex min-h-11 min-w-0 flex-1 flex-col justify-center py-2">
        <label
          htmlFor={id}
          className="min-w-0 text-sm break-words peer-disabled:cursor-not-allowed"
        >
          {label ?? (
            <>
              {t('notifications.preferences.unknownType')}{' '}
              <code className="text-xs break-all text-muted-foreground">
                {type}
              </code>
            </>
          )}
        </label>
        {item.mandatory ? (
          <span id={noteId} className="text-xs font-medium">
            {t('notifications.preferences.mandatory')}
          </span>
        ) : null}
      </div>
    </li>
  );
}
