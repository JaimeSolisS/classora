import { useQuery, useQueryClient } from '@tanstack/react-query';
import { useState } from 'react';
import { useTranslation } from 'react-i18next';
import { useDocumentTitle } from '@/app/useDocumentTitle';
import { useCenterTimeZone } from '@/features/auth/useCenterTimeZone';
import { useCan } from '@/features/auth/useSession';
import { todayInZone } from '@/lib/time';
import {
  BILLING_SETTINGS_KEY,
  fetchBillingSettings,
  updateBillingSettings,
} from './api';
import { BillingSettingsDialog } from './BillingSettingsDialog';
import {
  BillingSettingsView,
  type BillingSettingsState,
} from './BillingSettingsView';
import { billingUpdateBody } from './rules';
import { UNBILLED_QUERY_KEY } from '../unbilled/api';

// /billing/settings (#366 B), for billing.view holders: the billing start
// date, and for billing.manage holders "Change" (the dialog). After a save
// the settings and the unbilled list are invalidated and the status line
// says so; a version conflict's "Reload" refetches the settings.

export function BillingSettingsPage() {
  const { t } = useTranslation();
  useDocumentTitle(t('billing.settings.title'));
  const queryClient = useQueryClient();
  const timeZone = useCenterTimeZone();
  const canManage = useCan('billing.manage');
  const [changing, setChanging] = useState(false);
  const [announcement, setAnnouncement] = useState('');

  const detail = useQuery({
    queryKey: BILLING_SETTINGS_KEY,
    queryFn: ({ signal }) => fetchBillingSettings(signal),
  });

  const settings = detail.data;
  const state: BillingSettingsState = settings
    ? { status: 'ready', settings }
    : detail.isError
      ? {
          status: 'error',
          error: detail.error,
          onRetry: () => void detail.refetch(),
        }
      : { status: 'loading' };

  return (
    <>
      <BillingSettingsView
        state={state}
        canManage={canManage}
        onChange={() => {
          setAnnouncement('');
          setChanging(true);
        }}
        announcement={announcement}
      />
      {settings && canManage ? (
        <BillingSettingsDialog
          open={changing}
          settings={settings}
          today={todayInZone(timeZone)}
          onSubmit={async (values) => {
            const saved = await updateBillingSettings(
              billingUpdateBody(values, settings.version),
            );
            queryClient.setQueryData(BILLING_SETTINGS_KEY, saved);
            await queryClient.invalidateQueries({
              queryKey: UNBILLED_QUERY_KEY,
            });
          }}
          onReload={async () => {
            await detail.refetch();
          }}
          onClose={(saved) => {
            setChanging(false);
            if (saved) {
              setAnnouncement(t('billing.settings.saved'));
            }
          }}
        />
      ) : null}
    </>
  );
}
