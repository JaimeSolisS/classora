import { useQuery, useQueryClient } from '@tanstack/react-query';
import { useState } from 'react';
import { useTranslation } from 'react-i18next';
import { useDocumentTitle } from '@/app/useDocumentTitle';
import { useCenterTimeZone } from '@/features/auth/useCenterTimeZone';
import { useCan } from '@/features/auth/useSession';
import { todayInZone } from '@/lib/time';
import {
  BILLING_SETTINGS_KEY,
  CANCELLATION_RULES_KEY,
  createCancellationRule,
  deleteCancellationRule,
  fetchBillingSettings,
  fetchCancellationRules,
  updateBillingSettings,
  type CancellationRule,
} from './api';
import { BillingSettingsDialog } from './BillingSettingsDialog';
import { CancellationRuleDialog } from './CancellationRuleDialog';
import {
  CancellationRulesCard,
  type CancellationRulesState,
} from './CancellationRulesCard';
import {
  cancellationCreateBody,
  isApiCode,
  isNotFound,
  latestRule,
} from './cancellationRules';
import {
  BillingSettingsView,
  type BillingSettingsState,
} from './BillingSettingsView';
import { billingUpdateBody } from './rules';
import { UNBILLED_QUERY_KEY } from '../unbilled/api';

// /billing/settings (#366 B), for billing.view holders: the billing start
// date, and for billing.manage holders "Change" (the dialog). After a save
// the settings and the unbilled list are invalidated and the status line
// says so; a version conflict's "Reload" refetches the settings. Below it,
// the cancellation charges card (#385) with its own query: "Schedule a
// change" publishes a version built on the latest one, "Delete" removes an
// upcoming one; both refresh the card and announce in the same status line
// (a refused or vanished delete refreshes it too).

export function BillingSettingsPage() {
  const { t } = useTranslation();
  useDocumentTitle(t('billing.settings.title'));
  const queryClient = useQueryClient();
  const timeZone = useCenterTimeZone();
  const canManage = useCan('billing.manage');
  const [changing, setChanging] = useState(false);
  const [scheduling, setScheduling] = useState(false);
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

  const rules = useQuery({
    queryKey: CANCELLATION_RULES_KEY,
    queryFn: ({ signal }) => fetchCancellationRules(signal),
  });
  const overview = rules.data;
  const rulesState: CancellationRulesState = overview
    ? { status: 'ready', overview }
    : rules.isError
      ? {
          status: 'error',
          error: rules.error,
          onRetry: () => void rules.refetch(),
        }
      : { status: 'loading' };
  const refreshRules = () =>
    queryClient.invalidateQueries({ queryKey: CANCELLATION_RULES_KEY });

  async function removeRule(rule: CancellationRule) {
    setAnnouncement('');
    try {
      await deleteCancellationRule(rule.id);
    } catch (caught) {
      if (isNotFound(caught)) {
        await refreshRules();
        return;
      }
      if (isApiCode(caught, 'policy_version_in_effect')) {
        await refreshRules();
      }
      throw caught;
    }
    await refreshRules();
    setAnnouncement(t('billing.settings.cancellation.deleted'));
  }

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
      >
        <CancellationRulesCard
          state={rulesState}
          canManage={canManage}
          onSchedule={() => {
            setAnnouncement('');
            setScheduling(true);
          }}
          onDelete={removeRule}
        />
      </BillingSettingsView>
      {overview && canManage ? (
        <CancellationRuleDialog
          open={scheduling}
          latest={latestRule(overview)}
          today={todayInZone(timeZone)}
          timeZone={timeZone}
          onSubmit={async (values) => {
            await createCancellationRule(
              cancellationCreateBody(values, latestRule(overview), timeZone),
            );
            await refreshRules();
          }}
          onReload={async () => {
            await rules.refetch();
          }}
          onClose={(saved) => {
            setScheduling(false);
            if (saved) {
              setAnnouncement(t('billing.settings.cancellation.saved'));
            }
          }}
        />
      ) : null}
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
