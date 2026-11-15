import { useQuery, useQueryClient } from '@tanstack/react-query';
import { useId, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { useDocumentTitle } from '@/app/useDocumentTitle';
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
import { useCenterTimeZone } from '@/features/auth/useCenterTimeZone';
import { ConfirmDialog } from '@/features/guardians/ConfirmDialog';
import { toLanguage } from '@/i18n/language';
import { useFormatters } from '@/lib/formatters';
import { todayInZone, type LocalDate } from '@/lib/time';
import {
  createPolicy,
  deletePolicy,
  fetchPolicies,
  POLICIES_QUERY_KEY,
  type SchedulingPolicy,
  type SchedulingPolicyOverview,
} from './api';
import {
  changedRules,
  createBody,
  latestVersion,
  RULE_API_FIELDS,
  RULE_FIELDS,
  type PolicyFormValues,
  type RuleField,
} from './policyRules';
import { SchedulingPolicyForm } from './SchedulingPolicyForm';
import { SettingsTabs } from './SettingsTabs';

// /settings/scheduling-policies (#61 F2), for settings.manage holders: the
// version in force as plain sentences, the "Change policy" form, the
// upcoming versions (Delete behind a confirmation) and the history. Saving
// publishes a new version; the query is then invalidated.

export type PoliciesState =
  | { status: 'loading' }
  | { status: 'error'; error: unknown; onRetry: () => void }
  | { status: 'ready'; overview: SchedulingPolicyOverview };

interface SchedulingPoliciesViewProps {
  state: PoliciesState;
  /** Today's date in the center's zone. */
  today: LocalDate;
  timeZone: string;
  /** Publishes a version from the form; rejects with the error to show. */
  onCreate: (values: PolicyFormValues, basedOnId: string) => Promise<void>;
  /** Deletes an upcoming version; rejects with the error to show. */
  onDelete: (policy: SchedulingPolicy) => Promise<void>;
  onReload: () => Promise<void>;
}

/** A duration in whole hours or days, as words. */
function useDurations() {
  const { t } = useTranslation();
  return {
    hours: (count: number) =>
      count === 1
        ? t('settings.schedulingPolicies.units.hour')
        : t('settings.schedulingPolicies.units.hours', { count }),
    days: (count: number) =>
      count === 1
        ? t('settings.schedulingPolicies.units.day')
        : t('settings.schedulingPolicies.units.days', { count }),
  };
}

/** One rule's value as words ("24 hours", "Yes"). */
function useRuleValue() {
  const { t } = useTranslation();
  const { hours, days } = useDurations();
  return (policy: SchedulingPolicy, field: RuleField): string => {
    const value = policy[RULE_API_FIELDS[field]];
    if (typeof value === 'boolean') {
      return value
        ? t('settings.schedulingPolicies.yes')
        : t('settings.schedulingPolicies.no');
    }
    return field === 'makeupWindowDays' ? days(value) : hours(value);
  };
}

/** The rules of a version as plain sentences. */
function useRuleSentences() {
  const { t, i18n } = useTranslation();
  const { hours, days } = useDurations();
  const list = new Intl.ListFormat(toLanguage(i18n.resolvedLanguage), {
    type: 'conjunction',
  });
  return (policy: SchedulingPolicy): string[] => {
    const cases = [
      policy.makeup_after_on_time
        ? t('settings.schedulingPolicies.cases.onTime')
        : null,
      policy.makeup_after_late
        ? t('settings.schedulingPolicies.cases.late')
        : null,
      policy.makeup_after_no_show
        ? t('settings.schedulingPolicies.cases.noShow')
        : null,
    ].filter((item): item is string => item !== null);
    return [
      policy.change_notice_hours > 0
        ? t('settings.schedulingPolicies.rules.changeNotice', {
            duration: hours(policy.change_notice_hours),
          })
        : t('settings.schedulingPolicies.rules.changeNoticeNone'),
      t('settings.schedulingPolicies.rules.noShow'),
      t('settings.schedulingPolicies.rules.response', {
        duration: hours(policy.response_hours),
      }),
      policy.booking_notice_hours > 0
        ? t('settings.schedulingPolicies.rules.booking', {
            duration: hours(policy.booking_notice_hours),
          })
        : t('settings.schedulingPolicies.rules.bookingNone'),
      cases.length > 0
        ? t('settings.schedulingPolicies.rules.makeups', {
            cases: list.format(cases),
            duration: days(policy.makeup_window_days),
          })
        : t('settings.schedulingPolicies.rules.makeupsNone'),
      t('settings.schedulingPolicies.rules.excused'),
    ];
  };
}

/** The page without its query (stories render each state). */
export function SchedulingPoliciesView({
  state,
  today,
  timeZone,
  onCreate,
  onDelete,
  onReload,
}: SchedulingPoliciesViewProps) {
  const { t } = useTranslation();
  const id = useId();
  const { formatInstant } = useFormatters();
  const sentences = useRuleSentences();
  const ruleValue = useRuleValue();
  const [announcement, setAnnouncement] = useState('');
  const [error, setError] = useState<unknown>(null);
  const [deleting, setDeleting] = useState<SchedulingPolicy | null>(null);
  const ids = {
    current: `${id}-current`,
    form: `${id}-form`,
    upcoming: `${id}-upcoming`,
    history: `${id}-history`,
  };

  async function confirmDelete() {
    if (!deleting) {
      return;
    }
    setError(null);
    try {
      await onDelete(deleting);
      setAnnouncement(t('settings.schedulingPolicies.deleted'));
    } catch (caught) {
      setError(caught);
    } finally {
      setDeleting(null);
    }
  }

  let body;
  if (state.status === 'loading') {
    body = <LoadingState rows={8} />;
  } else if (state.status === 'error') {
    body = <ErrorAlert error={state.error} onRetry={state.onRetry} />;
  } else {
    const { current, upcoming, history } = state.overview;
    const latest = latestVersion(state.overview);
    // The version each one replaced: the one before it in time.
    const previous = [current, ...upcoming];
    const until = [current, ...history];
    body = (
      <>
        <section aria-labelledby={ids.current} className="flex flex-col gap-3">
          <h2 id={ids.current} className="text-lg font-semibold">
            {t('settings.schedulingPolicies.current.title')}
          </h2>
          <p className="text-sm text-muted-foreground">
            {t('settings.schedulingPolicies.current.since', {
              date: formatInstant(current.effective_from),
            })}
          </p>
          <ul className="flex list-disc flex-col gap-1 pl-5">
            {sentences(current).map((sentence) => (
              <li key={sentence}>{sentence}</li>
            ))}
          </ul>
        </section>

        <section aria-labelledby={ids.form} className="flex flex-col gap-3">
          <h2 id={ids.form} className="text-lg font-semibold">
            {t('settings.schedulingPolicies.form.title')}
          </h2>
          <SchedulingPolicyForm
            latest={latest}
            today={today}
            timeZone={timeZone}
            labelledBy={ids.form}
            onReload={onReload}
            onSubmit={async (values) => {
              setAnnouncement('');
              setError(null);
              await onCreate(values, latest.id);
              setAnnouncement(t('settings.schedulingPolicies.saved'));
            }}
          />
        </section>

        <section aria-labelledby={ids.upcoming} className="flex flex-col gap-3">
          <h2 id={ids.upcoming} className="text-lg font-semibold">
            {t('settings.schedulingPolicies.upcoming.title')}
          </h2>
          {upcoming.length === 0 ? (
            <p className="text-sm">
              {t('settings.schedulingPolicies.upcoming.empty')}
            </p>
          ) : (
            <Table aria-labelledby={ids.upcoming}>
              <TableHeader>
                <TableRow>
                  <TableHead scope="col">
                    {t('settings.schedulingPolicies.upcoming.takesEffect')}
                  </TableHead>
                  <TableHead scope="col">
                    {t('settings.schedulingPolicies.upcoming.changes')}
                  </TableHead>
                  <TableHead scope="col">
                    {t('settings.schedulingPolicies.upcoming.actions')}
                  </TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {upcoming.map((policy, index) => {
                  const before = previous[index]!;
                  const changes = changedRules(before, policy);
                  const date = formatInstant(policy.effective_from);
                  return (
                    <TableRow key={policy.id} data-policy-id={policy.id}>
                      <TableCell>{date}</TableCell>
                      <TableCell className="whitespace-normal">
                        {changes.length === 0 ? (
                          t('settings.schedulingPolicies.upcoming.noChanges')
                        ) : (
                          <ul className="flex flex-col gap-1">
                            {changes.map((field) => (
                              <li key={field}>
                                {t('settings.schedulingPolicies.change', {
                                  label: t(
                                    `settings.schedulingPolicies.labels.${field}`,
                                  ),
                                  before: ruleValue(before, field),
                                  after: ruleValue(policy, field),
                                })}
                              </li>
                            ))}
                          </ul>
                        )}
                      </TableCell>
                      <TableCell>
                        <Button
                          type="button"
                          variant="outline"
                          size="sm"
                          aria-label={t(
                            'settings.schedulingPolicies.upcoming.deleteFor',
                            { date },
                          )}
                          onClick={() => {
                            setError(null);
                            setAnnouncement('');
                            setDeleting(policy);
                          }}
                        >
                          {t('settings.schedulingPolicies.upcoming.delete')}
                        </Button>
                      </TableCell>
                    </TableRow>
                  );
                })}
              </TableBody>
            </Table>
          )}
        </section>

        <section aria-labelledby={ids.history} className="flex flex-col gap-3">
          <h2 id={ids.history} className="text-lg font-semibold">
            {t('settings.schedulingPolicies.history.title')}
          </h2>
          {history.length === 0 ? (
            <p className="text-sm">
              {t('settings.schedulingPolicies.history.empty')}
            </p>
          ) : (
            <Table aria-labelledby={ids.history}>
              <TableHeader>
                <TableRow>
                  <TableHead scope="col">
                    {t('settings.schedulingPolicies.history.from')}
                  </TableHead>
                  <TableHead scope="col">
                    {t('settings.schedulingPolicies.history.until')}
                  </TableHead>
                  <TableHead scope="col">
                    {t('settings.schedulingPolicies.history.values')}
                  </TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {history.map((policy, index) => (
                  <TableRow key={policy.id} data-policy-id={policy.id}>
                    <TableCell>
                      {formatInstant(policy.effective_from)}
                    </TableCell>
                    <TableCell>
                      {formatInstant(until[index]!.effective_from)}
                    </TableCell>
                    <TableCell className="whitespace-normal">
                      <ul className="flex flex-col gap-1">
                        {RULE_FIELDS.map((field) => (
                          <li key={field}>
                            {t('settings.schedulingPolicies.value', {
                              label: t(
                                `settings.schedulingPolicies.labels.${field}`,
                              ),
                              value: ruleValue(policy, field),
                            })}
                          </li>
                        ))}
                      </ul>
                    </TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          )}
        </section>
      </>
    );
  }

  return (
    <div className="flex flex-col gap-8">
      <SettingsTabs />
      <div className="flex flex-col gap-2">
        <h1 className="text-2xl font-semibold">
          {t('settings.schedulingPolicies.title')}
        </h1>
        <p className="text-sm text-muted-foreground">
          {t('settings.schedulingPolicies.help')}
        </p>
      </div>
      <p role="status" className="text-sm">
        {announcement}
      </p>
      {error ? <ErrorAlert error={error} /> : null}
      {body}
      <ConfirmDialog
        open={deleting !== null}
        title={t('settings.schedulingPolicies.deleteDialog.title')}
        body={t('settings.schedulingPolicies.deleteDialog.body', {
          date: deleting ? formatInstant(deleting.effective_from) : '',
        })}
        confirmLabel={t('settings.schedulingPolicies.deleteDialog.confirm')}
        destructive
        onConfirm={confirmDelete}
        onCancel={() => setDeleting(null)}
      />
    </div>
  );
}

export function SchedulingPoliciesPage() {
  const { t } = useTranslation();
  useDocumentTitle(t('settings.schedulingPolicies.title'));
  const queryClient = useQueryClient();
  const timeZone = useCenterTimeZone();
  const query = useQuery({
    queryKey: POLICIES_QUERY_KEY,
    queryFn: ({ signal }) => fetchPolicies(signal),
  });
  const state: PoliciesState = query.data
    ? { status: 'ready', overview: query.data }
    : query.isError
      ? {
          status: 'error',
          error: query.error,
          onRetry: () => void query.refetch(),
        }
      : { status: 'loading' };
  return (
    <SchedulingPoliciesView
      state={state}
      today={todayInZone(timeZone)}
      timeZone={timeZone}
      onCreate={async (values, basedOnId) => {
        await createPolicy(createBody(values, basedOnId, timeZone));
        await queryClient.invalidateQueries({ queryKey: POLICIES_QUERY_KEY });
      }}
      onDelete={async (policy) => {
        await deletePolicy(policy.id);
        await queryClient.invalidateQueries({ queryKey: POLICIES_QUERY_KEY });
      }}
      onReload={async () => {
        await query.refetch();
      }}
    />
  );
}
