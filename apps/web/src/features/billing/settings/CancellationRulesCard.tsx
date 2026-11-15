import { useId, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { ErrorAlert } from '@/components/feedback/ErrorAlert';
import { LoadingState } from '@/components/feedback/LoadingState';
import { Button } from '@/components/ui/button';
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from '@/components/ui/table';
import { ConfirmDialog } from '@/features/guardians/ConfirmDialog';
import { useFormatters } from '@/lib/formatters';
import type { CancellationRule, CancellationRuleOverview } from './api';
import {
  FIELD_KEYS,
  isApiCode,
  OUTCOME_FIELDS,
  outcomeSince,
  type OutcomeField,
} from './cancellationRules';

// The cancellation charges card (#385 A), without any query (stories render
// each state): three fixed facts (a makeup is always credited, a closure
// always credits, a reschedule keeps the charge), the rule in force as a
// four-row table (the excused row always reads "Always credited", #99 Q6),
// the upcoming versions (Delete behind a confirmation for billing.manage
// holders), and the earlier versions behind a toggle. "Schedule a change"
// opens the page's dialog. A refused delete (the version is already in
// force) says so; the page refreshes the overview.

export type CancellationRulesState =
  | { status: 'loading' }
  | { status: 'error'; error: unknown; onRetry: () => void }
  | { status: 'ready'; overview: CancellationRuleOverview };

export interface CancellationRulesCardProps {
  state: CancellationRulesState;
  /** billing.manage: "Schedule a change" and "Delete"; otherwise read-only. */
  canManage: boolean;
  onSchedule: () => void;
  /** Deletes an upcoming version; rejects with the error to show. */
  onDelete: (rule: CancellationRule) => Promise<void>;
  /** Stories: start with the earlier versions shown. */
  initialHistoryOpen?: boolean;
}

/** One outcome of a version as words; the excused one is always credited. */
function useOutcomeText() {
  const { t } = useTranslation();
  return (rule: CancellationRule, field: OutcomeField): string =>
    field === 'excused_outcome'
      ? t('billing.settings.cancellation.excusedAlways')
      : t(`billing.settings.cancellation.outcomes.${rule[field]}`);
}

function OutcomeList({ rule }: { rule: CancellationRule }) {
  const { t } = useTranslation();
  const outcomeText = useOutcomeText();
  return (
    <ul className="flex flex-col gap-1 text-sm">
      {OUTCOME_FIELDS.map((field) => (
        <li key={field}>
          {t('billing.settings.cancellation.value', {
            label: t(
              `billing.settings.cancellation.classes.${FIELD_KEYS[field]}`,
            ),
            value: outcomeText(rule, field),
          })}
        </li>
      ))}
    </ul>
  );
}

export function CancellationRulesCard({
  state,
  canManage,
  onSchedule,
  onDelete,
  initialHistoryOpen = false,
}: CancellationRulesCardProps) {
  const { t } = useTranslation();
  const { formatInstant } = useFormatters();
  const outcomeText = useOutcomeText();
  const id = useId();
  const ids = {
    card: `${id}-card`,
    inForce: `${id}-in-force`,
    upcoming: `${id}-upcoming`,
    historyList: `${id}-history-list`,
  };
  const [historyOpen, setHistoryOpen] = useState(initialHistoryOpen);
  const [deleting, setDeleting] = useState<CancellationRule | null>(null);
  const [failure, setFailure] = useState<unknown>(null);

  async function confirmDelete() {
    if (!deleting) {
      return;
    }
    setFailure(null);
    try {
      await onDelete(deleting);
    } catch (caught) {
      setFailure(caught);
    } finally {
      setDeleting(null);
    }
  }

  let body;
  if (state.status === 'loading') {
    body = <LoadingState rows={4} />;
  } else if (state.status === 'error') {
    body = <ErrorAlert error={state.error} onRetry={state.onRetry} />;
  } else {
    const { current, upcoming, history } = state.overview;
    // Each earlier version was in force until the one after it in time.
    const until = [current, ...history];
    body = (
      <>
        <div className="flex flex-col gap-2">
          <h3 id={ids.inForce} className="font-medium">
            {t('billing.settings.cancellation.inForce')}
          </h3>
          <Table aria-labelledby={ids.inForce}>
            <TableHeader>
              <TableRow>
                <TableHead scope="col">
                  {t('billing.settings.cancellation.columns.classification')}
                </TableHead>
                <TableHead scope="col">
                  {t('billing.settings.cancellation.columns.outcome')}
                </TableHead>
                <TableHead scope="col">
                  {t('billing.settings.cancellation.columns.since')}
                </TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {OUTCOME_FIELDS.map((field) => (
                <TableRow key={field} data-cancellation-class={field}>
                  <TableHead scope="row" className="font-normal">
                    {t(
                      `billing.settings.cancellation.classes.${FIELD_KEYS[field]}`,
                    )}
                  </TableHead>
                  <TableCell>{outcomeText(current, field)}</TableCell>
                  <TableCell>
                    {formatInstant(outcomeSince(state.overview, field), 'date')}
                  </TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        </div>

        <section aria-labelledby={ids.upcoming} className="flex flex-col gap-2">
          <h3 id={ids.upcoming} className="font-medium">
            {t('billing.settings.cancellation.upcoming.title')}
          </h3>
          {upcoming.length === 0 ? (
            <p className="text-sm">
              {t('billing.settings.cancellation.upcoming.empty')}
            </p>
          ) : (
            <ul className="flex flex-col gap-3">
              {upcoming.map((rule) => {
                const date = formatInstant(rule.effective_from);
                return (
                  <li
                    key={rule.id}
                    data-rule-id={rule.id}
                    className="flex flex-wrap items-start justify-between gap-3 rounded-md border border-border p-3"
                  >
                    <div className="flex flex-col gap-1">
                      <p className="text-sm font-medium">
                        {t(
                          'billing.settings.cancellation.upcoming.takesEffect',
                          {
                            date,
                          },
                        )}
                      </p>
                      <OutcomeList rule={rule} />
                    </div>
                    {canManage ? (
                      <Button
                        type="button"
                        variant="outline"
                        size="sm"
                        aria-label={t(
                          'billing.settings.cancellation.upcoming.deleteFor',
                          { date },
                        )}
                        onClick={() => {
                          setFailure(null);
                          setDeleting(rule);
                        }}
                      >
                        {t('billing.settings.cancellation.upcoming.delete')}
                      </Button>
                    ) : null}
                  </li>
                );
              })}
            </ul>
          )}
        </section>

        <div className="flex flex-col gap-2">
          {history.length === 0 ? (
            <p className="text-sm">
              {t('billing.settings.cancellation.history.empty')}
            </p>
          ) : (
            <>
              <div>
                <Button
                  type="button"
                  variant="ghost"
                  size="sm"
                  aria-expanded={historyOpen}
                  aria-controls={ids.historyList}
                  onClick={() => setHistoryOpen(!historyOpen)}
                >
                  {historyOpen
                    ? t('billing.settings.cancellation.history.hide')
                    : t('billing.settings.cancellation.history.show', {
                        count: history.length,
                      })}
                </Button>
              </div>
              {historyOpen ? (
                <ul id={ids.historyList} className="flex flex-col gap-3">
                  {history.map((rule, index) => (
                    <li
                      key={rule.id}
                      data-rule-id={rule.id}
                      className="flex flex-col gap-1 rounded-md border border-border p-3"
                    >
                      <p className="text-sm font-medium">
                        {t('billing.settings.cancellation.history.range', {
                          from: formatInstant(rule.effective_from),
                          until: formatInstant(until[index]!.effective_from),
                        })}
                      </p>
                      <OutcomeList rule={rule} />
                    </li>
                  ))}
                </ul>
              ) : null}
            </>
          )}
        </div>
      </>
    );
  }

  const inForce = isApiCode(failure, 'policy_version_in_effect');

  return (
    <section
      aria-labelledby={ids.card}
      className="flex max-w-3xl flex-col gap-4 rounded-lg border border-border p-4"
    >
      <div className="flex flex-wrap items-center justify-between gap-4">
        <h2 id={ids.card} className="text-lg font-semibold">
          {t('billing.settings.cancellation.title')}
        </h2>
        {canManage && state.status === 'ready' ? (
          <Button type="button" variant="outline" onClick={onSchedule}>
            {t('billing.settings.cancellation.schedule')}
          </Button>
        ) : null}
      </div>
      <ul className="flex list-disc flex-col gap-1 pl-5 text-sm text-muted-foreground">
        <li>{t('billing.settings.cancellation.facts.makeup')}</li>
        <li>{t('billing.settings.cancellation.facts.closure')}</li>
        <li>{t('billing.settings.cancellation.facts.reschedule')}</li>
      </ul>
      {inForce ? (
        <p role="alert" className="text-sm font-medium text-destructive">
          {t('billing.settings.cancellation.alreadyInForce')}
        </p>
      ) : failure ? (
        <ErrorAlert error={failure} />
      ) : null}
      {body}
      <ConfirmDialog
        open={deleting !== null}
        title={t('billing.settings.cancellation.deleteDialog.title')}
        body={t('billing.settings.cancellation.deleteDialog.body', {
          date: deleting ? formatInstant(deleting.effective_from) : '',
        })}
        confirmLabel={t('billing.settings.cancellation.deleteDialog.confirm')}
        destructive
        onConfirm={confirmDelete}
        onCancel={() => setDeleting(null)}
      />
    </section>
  );
}
