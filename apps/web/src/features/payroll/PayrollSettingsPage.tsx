import { useQuery, useQueryClient } from '@tanstack/react-query';
import { useState } from 'react';
import { useTranslation } from 'react-i18next';
import { useDocumentTitle } from '@/app/useDocumentTitle';
import { useCan } from '@/features/auth/useSession';
import {
  fetchPayrollSettings,
  PAYROLL_SETTINGS_KEY,
  updatePayrollSettings,
} from './api';
import { PayrollSettingsDialog } from './PayrollSettingsDialog';
import {
  PayrollSettingsView,
  type PayrollSettingsState,
} from './PayrollSettingsView';
import { settingsUpdateBody } from './rules';

// /payroll/settings (#425 D), for payroll.view holders; "Change" and the
// dialog for payroll.manage holders. After a save the answer replaces the
// cached settings and the status line says so.

export function PayrollSettingsPage() {
  const { t } = useTranslation();
  useDocumentTitle(t('payroll.settings.title'));
  const queryClient = useQueryClient();
  const canManage = useCan('payroll.manage');
  const [changing, setChanging] = useState(false);
  const [announcement, setAnnouncement] = useState('');

  const detail = useQuery({
    queryKey: PAYROLL_SETTINGS_KEY,
    queryFn: ({ signal }) => fetchPayrollSettings(signal),
  });
  const settings = detail.data;
  const state: PayrollSettingsState = settings
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
      <PayrollSettingsView
        state={state}
        canManage={canManage}
        onChange={() => {
          setAnnouncement('');
          setChanging(true);
        }}
        announcement={announcement}
      />
      {settings && canManage ? (
        <PayrollSettingsDialog
          open={changing}
          settings={settings}
          onSubmit={async (values, base) => {
            const saved = await updatePayrollSettings(
              settingsUpdateBody(values, base),
            );
            queryClient.setQueryData(PAYROLL_SETTINGS_KEY, saved);
          }}
          onReload={async () => {
            await detail.refetch();
          }}
          onClose={(saved) => {
            setChanging(false);
            if (saved) {
              setAnnouncement(t('payroll.settings.saved'));
            }
          }}
        />
      ) : null}
    </>
  );
}
