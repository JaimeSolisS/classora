import { useQuery, useQueryClient } from '@tanstack/react-query';
import { useState } from 'react';
import { useTranslation } from 'react-i18next';
import { ApiError } from '@/app/api';
import { useDocumentTitle } from '@/app/useDocumentTitle';
import {
  CATALOGUE_CHANGED_CODES,
  fetchNotificationPreferences,
  NOTIFICATION_PREFERENCES_KEY,
  updateNotificationPreference,
  type NotificationPreferences,
} from './preferences-api';
import {
  NotificationPreferencesView,
  type PreferencesState,
} from './NotificationPreferencesView';

interface SaveFailure {
  error: unknown;
  type: string;
  enabled: boolean;
}

// /account/notifications (M8-02b, #119): any signed-in user turns optional
// notification types on or off. Each checkbox saves on change with a
// one-item PATCH (E2) and is disabled while it runs; the answer is the whole
// catalogue, shown as is, and "Saved." is announced politely. A failed save
// shows the saved value again with an alert and "Try again" (a lost answer
// is harmless: set semantics). A refusal saying the catalogue changed
// (`notification_type_unknown` / `_mandatory`) reloads it and says so; if
// the reload fails the shown list stays with a stale alert (B5, #458).
export function NotificationPreferencesPage() {
  const { t } = useTranslation();
  useDocumentTitle(t('notifications.preferences.title'));
  const queryClient = useQueryClient();
  const query = useQuery({
    queryKey: NOTIFICATION_PREFERENCES_KEY,
    queryFn: ({ signal }) => fetchNotificationPreferences(signal),
  });
  const [saving, setSaving] = useState<ReadonlyMap<string, boolean>>(
    () => new Map(),
  );
  const [saveFailure, setSaveFailure] = useState<SaveFailure | null>(null);
  const [refreshError, setRefreshError] = useState<unknown>(null);
  const [refreshing, setRefreshing] = useState(false);
  // The same text twice in a row must be announced twice: the second one
  // differs by a trailing no-break space (trimmed away by readers).
  const [announcement, setAnnouncement] = useState({ text: '', odd: false });
  const announce = (text: string) =>
    setAnnouncement((previous) => ({ text, odd: !previous.odd }));

  async function reload() {
    setRefreshing(true);
    const result = await query.refetch();
    setRefreshing(false);
    if (result.isError) {
      setRefreshError(result.error);
      announce('');
    } else {
      setRefreshError(null);
      announce(t('notifications.preferences.reloaded'));
    }
  }

  async function save(type: string, enabled: boolean) {
    setSaveFailure(null);
    setSaving((previous) => new Map(previous).set(type, enabled));
    try {
      const body = await updateNotificationPreference(type, enabled);
      queryClient.setQueryData<NotificationPreferences>(
        NOTIFICATION_PREFERENCES_KEY,
        body,
      );
      setRefreshError(null);
      announce(t('notifications.preferences.saved'));
    } catch (error) {
      if (
        error instanceof ApiError &&
        CATALOGUE_CHANGED_CODES.has(error.code)
      ) {
        await reload();
      } else {
        setSaveFailure({ error, type, enabled });
        // The alert (role="alert") says it; "Saved." must not linger.
        announce('');
      }
    } finally {
      setSaving((previous) => {
        const next = new Map(previous);
        next.delete(type);
        return next;
      });
    }
  }

  const state: PreferencesState = query.data
    ? {
        status: 'ready',
        items: query.data.items,
        saving,
        onToggle: (type, enabled) => void save(type, enabled),
      }
    : query.isError
      ? {
          status: 'error',
          error: query.error,
          onRetry: () => void query.refetch(),
        }
      : { status: 'loading' };

  return (
    <NotificationPreferencesView
      state={state}
      saveError={
        saveFailure
          ? {
              error: saveFailure.error,
              onRetry: () => void save(saveFailure.type, saveFailure.enabled),
            }
          : null
      }
      refreshError={
        refreshError
          ? {
              error: refreshError,
              onRetry: () => void reload(),
              retrying: refreshing,
            }
          : null
      }
      announcement={
        announcement.text + (announcement.text && announcement.odd ? ' ' : '')
      }
    />
  );
}
