import { useId, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { ErrorAlert } from '@/components/feedback/ErrorAlert';
import { LoadingState } from '@/components/feedback/LoadingState';
import { Button } from '@/components/ui/button';
import { ConfirmDialog } from '@/features/guardians/ConfirmDialog';
import { useFormatters } from '@/lib/formatters';
import type {
  PayRule,
  PayRuleEvent,
  PayRuleEventOverview,
  PayRuleOverview,
} from './api';
import { VersionRow } from './CompensationView';
import { PAY_RULE_EVENTS, percentText } from './payRuleForm';
import { PayrollTabs } from './PayrollTabs';

// The Pay rules tab (#444 B), without any query: one block per event with the
// rule in force as a sentence and its date, the upcoming versions and the
// earlier ones behind a toggle. Versions are cards at every width (#425 D6).
// `payroll.manage` adds "New version" and Delete on an upcoming version;
// without it the page is read-only. A refused delete shows in its event's
// block. No amount is computed here: each is the API's string, formatted.

export type PayRulesState =
  | { status: 'loading' }
  | { status: 'error'; error: unknown; onRetry: () => void }
  | { status: 'ready'; overview: PayRuleOverview };

export interface PayRulesViewProps {
  state: PayRulesState;
  canManage: boolean;
  onNewVersion: (event: PayRuleEvent) => void;
  /** Deletes an upcoming version; rejects with the error to show. */
  onDelete: (rule: PayRule) => Promise<void>;
  /** Read by the status line after a save or a delete. */
  announcement?: string;
  /** Stories: start with the earlier versions shown. */
  initialHistoryOpen?: boolean;
}

function usePayRuleText() {
  const { t } = useTranslation();
  const { formatMoney } = useFormatters();
  return (rule: PayRule): string => {
    if (rule.mode === 'percent' && rule.percent !== null) {
      return t('payroll.payRules.sentences.percent', {
        percent: percentText(rule.percent),
      });
    }
    if (rule.mode === 'fixed' && rule.amount !== null) {
      return t('payroll.payRules.sentences.fixed', {
        amount: formatMoney(rule.amount),
      });
    }
    return t('payroll.payRules.sentences.none');
  };
}

function EventBlock({
  event,
  rules,
  canManage,
  failure,
  onNewVersion,
  onDelete,
  initialHistoryOpen,
}: {
  event: PayRuleEvent;
  rules: PayRuleEventOverview;
  canManage: boolean;
  failure: unknown;
  onNewVersion: () => void;
  onDelete: (rule: PayRule) => void;
  initialHistoryOpen: boolean;
}) {
  const { t } = useTranslation();
  const { formatLocalDate } = useFormatters();
  const text = usePayRuleText();
  const id = useId();
  const ids = {
    heading: `${id}-heading`,
    upcoming: `${id}-upcoming`,
    history: `${id}-history`,
  };
  const [historyOpen, setHistoryOpen] = useState(initialHistoryOpen);
  const { current, upcoming, history } = rules;
  const eventName = t(`payroll.payRules.events.${event}`);
  // Each earlier version was in force until the one after it in time.
  const until = [current, ...history];

  return (
    <section
      aria-labelledby={ids.heading}
      data-pay-rule-event={event}
      className="flex min-w-0 flex-col gap-4 rounded-lg border border-border p-4"
    >
      <div className="flex flex-wrap items-center justify-between gap-4">
        <h2 id={ids.heading} className="text-lg font-semibold">
          {eventName}
        </h2>
        {canManage ? (
          <Button
            type="button"
            variant="outline"
            className="min-h-11 md:min-h-9"
            aria-label={t('payroll.payRules.newVersionFor', {
              event: eventName,
            })}
            onClick={onNewVersion}
          >
            {t('payroll.payRules.newVersion')}
          </Button>
        ) : null}
      </div>
      {event === 'late_cancellation' ? (
        <p className="text-sm text-muted-foreground" data-pay-rule-late-note>
          {t('payroll.payRules.lateNote')}
        </p>
      ) : null}
      {failure ? <ErrorAlert error={failure} /> : null}
      <ul className="flex flex-col gap-2">
        <VersionRow
          id={current.id}
          status="current"
          date={t('payroll.compensation.since', {
            date: formatLocalDate(current.effective_from),
          })}
          value={text(current)}
          deleteLabel=""
        />
      </ul>
      <div className="flex flex-col gap-2">
        <h3 id={ids.upcoming} className="font-medium">
          {t('payroll.payRules.upcoming')}
        </h3>
        {upcoming.length === 0 ? (
          <p className="text-sm">{t('payroll.payRules.noUpcoming')}</p>
        ) : (
          <ul aria-labelledby={ids.upcoming} className="flex flex-col gap-2">
            {upcoming.map((rule) => (
              <VersionRow
                key={rule.id}
                id={rule.id}
                status="upcoming"
                date={t('payroll.compensation.since', {
                  date: formatLocalDate(rule.effective_from),
                })}
                value={text(rule)}
                deleteLabel={t('payroll.payRules.deleteFor', {
                  event: eventName,
                  date: formatLocalDate(rule.effective_from),
                })}
                onDelete={canManage ? () => onDelete(rule) : undefined}
              />
            ))}
          </ul>
        )}
      </div>
      {history.length > 0 ? (
        <div className="flex flex-col gap-2">
          <div>
            <Button
              type="button"
              variant="ghost"
              size="sm"
              className="min-h-11 md:min-h-8"
              aria-expanded={historyOpen}
              aria-controls={ids.history}
              onClick={() => setHistoryOpen(!historyOpen)}
            >
              {historyOpen
                ? t('payroll.payRules.historyHide')
                : t('payroll.payRules.historyShow', { count: history.length })}
            </Button>
          </div>
          {historyOpen ? (
            <ul id={ids.history} className="flex flex-col gap-2">
              {history.map((rule, index) => (
                <VersionRow
                  key={rule.id}
                  id={rule.id}
                  status="past"
                  date={t('payroll.payRules.range', {
                    from: formatLocalDate(rule.effective_from),
                    until: formatLocalDate(until[index]!.effective_from),
                  })}
                  value={text(rule)}
                  deleteLabel=""
                />
              ))}
            </ul>
          ) : null}
        </div>
      ) : null}
    </section>
  );
}

export function PayRulesView({
  state,
  canManage,
  onNewVersion,
  onDelete,
  announcement = '',
  initialHistoryOpen = false,
}: PayRulesViewProps) {
  const { t } = useTranslation();
  const { formatLocalDate } = useFormatters();
  const text = usePayRuleText();
  const [deleting, setDeleting] = useState<PayRule | null>(null);
  const [refusal, setRefusal] = useState<{
    event: PayRuleEvent;
    error: unknown;
  } | null>(null);

  async function confirmDelete() {
    if (!deleting) {
      return;
    }
    const rule = deleting;
    setRefusal(null);
    try {
      await onDelete(rule);
    } catch (caught) {
      setRefusal({ event: rule.event, error: caught });
    } finally {
      setDeleting(null);
    }
  }

  let body;
  if (state.status === 'loading') {
    body = <LoadingState rows={6} />;
  } else if (state.status === 'error') {
    body = <ErrorAlert error={state.error} onRetry={state.onRetry} />;
  } else {
    const { overview } = state;
    body = PAY_RULE_EVENTS.map((event) => (
      <EventBlock
        key={event}
        event={event}
        rules={overview[event]}
        canManage={canManage}
        failure={refusal?.event === event ? refusal.error : null}
        onNewVersion={() => {
          setRefusal(null);
          onNewVersion(event);
        }}
        onDelete={(rule) => {
          setRefusal(null);
          setDeleting(rule);
        }}
        initialHistoryOpen={initialHistoryOpen}
      />
    ));
  }

  return (
    <div className="flex min-w-0 flex-col gap-8">
      <PayrollTabs />
      <div className="flex flex-col gap-2">
        <h1 className="text-2xl font-semibold">
          {t('payroll.payRules.title')}
        </h1>
        <p className="max-w-3xl text-sm text-muted-foreground">
          {t('payroll.payRules.intro')}
        </p>
        <p className="max-w-3xl text-sm text-muted-foreground">
          {t('payroll.payRules.versionsHelp')}
        </p>
      </div>
      {body}
      <p role="status" className="text-sm">
        {announcement}
      </p>
      <ConfirmDialog
        open={deleting !== null}
        title={t('payroll.deleteDialog.title')}
        body={
          deleting
            ? t('payroll.payRules.deleteBody', {
                event: t(`payroll.payRules.events.${deleting.event}`),
                date: formatLocalDate(deleting.effective_from),
                value: text(deleting),
              })
            : ''
        }
        confirmLabel={t('payroll.deleteDialog.confirm')}
        destructive
        onConfirm={confirmDelete}
        onCancel={() => setDeleting(null)}
      />
    </div>
  );
}
