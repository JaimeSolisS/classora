import { useQuery, useQueryClient } from '@tanstack/react-query';
import { useState } from 'react';
import { useTranslation } from 'react-i18next';
import { ApiError } from '@/app/api';
import { useDocumentTitle } from '@/app/useDocumentTitle';
import { useCan } from '@/features/auth/useSession';
import { parseLocalDate } from '@/lib/time';
import {
  createPayRule,
  deletePayRule,
  fetchPayRules,
  PAY_RULES_KEY,
  UNRESOLVED_KEY,
  type PayRule,
  type PayRuleEvent,
} from './api';
import { PayRuleDialog } from './PayRuleDialog';
import {
  latestPayRule,
  payRuleCreateBody,
  type PayRuleFormValues,
} from './payRuleForm';
import { PayRulesView, type PayRulesState } from './PayRulesView';

// /payroll/pay-rules (#444 B), for payroll.view holders; "New version" and
// Delete for payroll.manage holders. A write refreshes the rules, and the
// unresolved work too: whether a cancellation is listed depends on its rule.

export function PayRulesPage() {
  const { t } = useTranslation();
  useDocumentTitle(t('payroll.payRules.title'));
  const queryClient = useQueryClient();
  const canManage = useCan('payroll.manage');
  const [adding, setAdding] = useState<PayRuleEvent | null>(null);
  const [announcement, setAnnouncement] = useState('');

  const detail = useQuery({
    queryKey: PAY_RULES_KEY,
    queryFn: ({ signal }) => fetchPayRules(signal),
  });
  const overview = detail.data;
  const state: PayRulesState = overview
    ? { status: 'ready', overview }
    : detail.isError
      ? {
          status: 'error',
          error: detail.error,
          onRetry: () => void detail.refetch(),
        }
      : { status: 'loading' };

  async function refresh() {
    await Promise.all([
      queryClient.invalidateQueries({ queryKey: PAY_RULES_KEY }),
      queryClient.invalidateQueries({ queryKey: UNRESOLVED_KEY }),
    ]);
  }

  async function publish(values: PayRuleFormValues, base: PayRule) {
    await createPayRule(payRuleCreateBody(values, base));
    await refresh();
  }

  async function remove(rule: PayRule) {
    setAnnouncement('');
    try {
      await deletePayRule(rule.id);
    } catch (caught) {
      // Every refusal means the list on screen is out of date.
      await refresh();
      if (caught instanceof ApiError && caught.status === 404) {
        setAnnouncement(t('payroll.payRules.alreadyGone'));
        return;
      }
      throw caught;
    }
    await refresh();
    setAnnouncement(t('payroll.payRules.deleted'));
  }

  return (
    <>
      <PayRulesView
        state={state}
        canManage={canManage}
        onNewVersion={(event) => {
          setAnnouncement('');
          setAdding(event);
        }}
        onDelete={remove}
        announcement={announcement}
      />
      {overview && canManage && adding ? (
        <PayRuleDialog
          key={adding}
          open
          latest={latestPayRule(overview[adding])}
          today={parseLocalDate(overview.today)}
          onSubmit={publish}
          onReload={async () => {
            await detail.refetch();
          }}
          onClose={(saved) => {
            setAdding(null);
            if (saved) {
              setAnnouncement(t('payroll.payRules.saved'));
            }
          }}
        />
      ) : null}
    </>
  );
}
