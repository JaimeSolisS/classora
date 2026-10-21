import { useQuery, useQueryClient } from '@tanstack/react-query';
import { useId, useMemo } from 'react';
import { useTranslation } from 'react-i18next';
import { useDocumentTitle } from '@/app/useDocumentTitle';
import { ErrorAlert } from '@/components/feedback/ErrorAlert';
import { LoadingState } from '@/components/feedback/LoadingState';
import { SESSION_QUERY_KEY } from '@/features/auth/session';
import {
  fetchSettings,
  SETTINGS_QUERY_KEY,
  updateSettings,
  type BusinessSettings,
} from './api';
import { updateBody, valuesOf, type SettingsFormValues } from './rules';
import { SettingsForm } from './SettingsForm';
import { SettingsTabs } from './SettingsTabs';

// /settings (#37 H3, H4), for settings.manage holders: the business
// settings form. A save sends `version` plus the changed fields, stores the
// answer and invalidates the session (/auth/me), whose center_time_zone the
// formatters read, so a new zone applies at once.

interface SettingsViewProps {
  /** The stored settings; undefined while loading or after an error. */
  settings: BusinessSettings | undefined;
  /** The failed load, if any. */
  error: unknown;
  onRetry: () => void;
  onSubmit: (values: SettingsFormValues) => Promise<void>;
  onReload: () => Promise<void>;
}

/** The page without its query (stories render each state). */
export function SettingsView({
  settings,
  error,
  onRetry,
  onSubmit,
  onReload,
}: SettingsViewProps) {
  const { t } = useTranslation();
  const titleId = useId();
  const defaultValues = useMemo(
    () => (settings ? valuesOf(settings) : null),
    [settings],
  );
  return (
    <div className="flex flex-col gap-6">
      <SettingsTabs />
      <h1 id={titleId} className="text-2xl font-semibold">
        {t('settings.title')}
      </h1>
      {defaultValues ? (
        <SettingsForm
          defaultValues={defaultValues}
          labelledBy={titleId}
          onSubmit={onSubmit}
          onReload={onReload}
        />
      ) : error ? (
        <ErrorAlert error={error} onRetry={onRetry} />
      ) : (
        <LoadingState rows={8} />
      )}
    </div>
  );
}

export function SettingsPage() {
  const { t } = useTranslation();
  useDocumentTitle(t('settings.title'));
  const queryClient = useQueryClient();
  const query = useQuery({
    queryKey: SETTINGS_QUERY_KEY,
    queryFn: ({ signal }) => fetchSettings(signal),
  });
  return (
    <SettingsView
      settings={query.data}
      error={query.isError ? query.error : null}
      onRetry={() => void query.refetch()}
      onSubmit={async (values) => {
        const current =
          queryClient.getQueryData<BusinessSettings>(SETTINGS_QUERY_KEY) ??
          query.data;
        if (!current) {
          return;
        }
        const saved = await updateSettings(updateBody(current, values));
        queryClient.setQueryData(SETTINGS_QUERY_KEY, saved);
        void queryClient.invalidateQueries({ queryKey: SETTINGS_QUERY_KEY });
        void queryClient.invalidateQueries({ queryKey: SESSION_QUERY_KEY });
      }}
      onReload={async () => {
        await query.refetch();
      }}
    />
  );
}
