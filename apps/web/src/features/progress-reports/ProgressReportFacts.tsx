import { useId, type ReactNode } from 'react';
import { useTranslation } from 'react-i18next';
import { hundredthsOf } from '@/lib/decimal';
import { useFormatters } from '@/lib/formatters';
import type { ReportFacts } from './api';
import { decimalText, FACT_EVALUATIONS_MAX, FACT_PLANS_MAX } from './rules';

// The facts the server compiled for a report (#315 D; #83 B2, schema
// version 1), shared by the editor's preview, the review page and every
// approved report. Text first: each block is a section with its heading
// and sentences; numbers are the API's (decimal strings shown through
// lib/decimal, never parsed as floats; no client totals or averages); a
// null average reads "No average", never 0 %. A plan's progress bar is
// decorative (aria-hidden): its value is in the text next to it. Any other
// schema version shows only "can't be shown". Plain text everywhere; no
// requests, no actions.

/** A decorative bar's width, 0-100 (whole percent, from the string). */
function barWidth(percent: string): number {
  const hundredths = hundredthsOf(percent);
  if (hundredths === null) {
    return 0;
  }
  return Math.min(100, Math.max(0, Math.round(hundredths / 100)));
}

function Block({ title, children }: { title: string; children: ReactNode }) {
  const id = useId();
  return (
    <section aria-labelledby={id} className="flex min-w-0 flex-col gap-2">
      <h3 id={id} className="text-base font-semibold">
        {title}
      </h3>
      {children}
    </section>
  );
}

export function ProgressReportFacts({
  facts,
  heading,
  note,
  action,
}: {
  facts: ReportFacts;
  /** "Facts preview", "Facts as submitted on …", "Facts as approved". */
  heading: string;
  /** A line under the heading (the preview's note). */
  note?: string;
  /** The preview's "Refresh facts" button. */
  action?: ReactNode;
}) {
  const { t, i18n } = useTranslation();
  const { formatLocalDate } = useFormatters();
  const id = useId();
  const tAny = t as unknown as (key: string) => string;
  const known = (key: string, fallback: string) =>
    i18n.exists(key) ? tAny(key) : fallback;

  const header = (
    <div className="flex min-w-0 flex-col gap-1">
      <h2 id={id} className="text-lg font-semibold break-words">
        {heading}
      </h2>
      {note ? <p className="text-sm text-muted-foreground">{note}</p> : null}
      {action}
    </div>
  );

  if ((facts as { schema_version: unknown }).schema_version !== 1) {
    return (
      <section
        aria-labelledby={id}
        className="flex min-w-0 flex-col gap-3 rounded-md border border-border p-4"
      >
        {header}
        <p className="text-sm">{t('progressReports.facts.unsupported')}</p>
      </section>
    );
  }

  const { attendance, evaluations, assignments } = facts;
  const plans = facts.learning_plans;
  return (
    <section
      aria-labelledby={id}
      className="flex min-w-0 flex-col gap-4 rounded-md border border-border p-4"
    >
      {header}
      <p className="text-sm break-words">
        {t('progressReports.facts.periodLine', {
          start: formatLocalDate(facts.period.start),
          end: formatLocalDate(facts.period.end),
          subject: facts.subject?.name ?? t('progressReports.allSubjects'),
        })}
      </p>

      <Block title={t('progressReports.facts.attendance.title')}>
        <p className="text-sm">
          {attendance.sessions === 0
            ? t('progressReports.facts.attendance.none')
            : t('progressReports.facts.attendance.line', {
                count: attendance.sessions,
                present: attendance.present,
                late: attendance.late,
                absent: attendance.absent,
                notRecorded: attendance.not_recorded,
              })}
        </p>
      </Block>

      <Block title={t('progressReports.facts.evaluations.title')}>
        {evaluations.count === 0 ? (
          <p className="text-sm">
            {t('progressReports.facts.evaluations.none')}
          </p>
        ) : (
          <>
            <p className="text-sm">
              {evaluations.average_percent === null
                ? t('progressReports.facts.evaluations.countNoAverage', {
                    count: evaluations.count,
                  })
                : t('progressReports.facts.evaluations.countAverage', {
                    count: evaluations.count,
                    average: decimalText(evaluations.average_percent),
                  })}
            </p>
            {evaluations.by_subject.length > 0 ? (
              <ul className="flex min-w-0 flex-col gap-1 text-sm">
                {evaluations.by_subject.map((row) => (
                  <li key={row.subject.id} className="break-words">
                    {row.average_percent === null
                      ? t(
                          'progressReports.facts.evaluations.subjectNoAverage',
                          {
                            subject: row.subject.name,
                            count: row.count,
                          },
                        )
                      : t('progressReports.facts.evaluations.subjectAverage', {
                          subject: row.subject.name,
                          count: row.count,
                          average: decimalText(row.average_percent),
                        })}
                  </li>
                ))}
              </ul>
            ) : null}
            {evaluations.entries.length > 0 ? (
              <ul className="flex min-w-0 flex-col gap-1 border-t border-border pt-2 text-sm">
                {evaluations.entries.map((entry, index) => (
                  <li
                    key={`${entry.evaluated_on}-${index}`}
                    className="break-words"
                  >
                    {t('progressReports.facts.evaluations.entry', {
                      date: formatLocalDate(entry.evaluated_on),
                      subject: entry.subject.name,
                      kind: known(
                        `academics.kinds.${entry.evaluation_kind}`,
                        entry.evaluation_kind,
                      ),
                      template: entry.template_name,
                      percent: decimalText(entry.score_percent),
                    })}
                  </li>
                ))}
              </ul>
            ) : null}
            {evaluations.truncated ? (
              <p className="text-sm text-muted-foreground">
                {t('progressReports.facts.evaluations.truncated', {
                  max: FACT_EVALUATIONS_MAX,
                })}
              </p>
            ) : null}
          </>
        )}
      </Block>

      <Block title={t('progressReports.facts.homework.title')}>
        {assignments.count === 0 ? (
          <p className="text-sm">{t('progressReports.facts.homework.none')}</p>
        ) : (
          <p className="text-sm">
            {[
              t('progressReports.facts.homework.line', {
                count: assignments.count,
                pending: assignments.pending,
                missing: assignments.missing,
                submitted: assignments.submitted,
                graded: assignments.graded,
              }),
              assignments.late > 0
                ? t('progressReports.facts.homework.late', {
                    count: assignments.late,
                  })
                : null,
              assignments.average_percent === null
                ? t('progressReports.facts.noAverage')
                : t('progressReports.facts.average', {
                    average: decimalText(assignments.average_percent),
                  }),
            ]
              .filter(Boolean)
              .join(' · ')}
          </p>
        )}
      </Block>

      <Block title={t('progressReports.facts.plans.title')}>
        {plans.length === 0 ? (
          <p className="text-sm">{t('progressReports.facts.plans.none')}</p>
        ) : (
          <ul className="flex min-w-0 flex-col gap-3">
            {plans.map((plan, index) => (
              <li
                key={`${plan.title}-${index}`}
                className="flex min-w-0 flex-col gap-1 text-sm"
              >
                <p className="font-medium break-words">{plan.title}</p>
                <p className="break-words text-muted-foreground">
                  {t('progressReports.facts.plans.subjectStatus', {
                    subject: plan.subject.name,
                    status: known(
                      `learningPlans.status.${plan.status}`,
                      plan.status,
                    ),
                  })}
                </p>
                <div className="flex min-w-0 flex-wrap items-center gap-x-3 gap-y-1">
                  <div
                    aria-hidden="true"
                    className="h-2.5 w-32 shrink-0 overflow-hidden rounded-full border border-input bg-muted"
                  >
                    <div
                      className="h-full bg-primary"
                      style={{ width: `${barWidth(plan.progress_percent)}%` }}
                    />
                  </div>
                  <span>
                    {t('progressReports.facts.plans.progress', {
                      percent: plan.progress_percent,
                    })}
                  </span>
                </div>
                <p>
                  {t('progressReports.facts.plans.counts', {
                    objectivesDone: plan.objectives_completed,
                    objectivesTotal: plan.objectives_total,
                    milestonesDone: plan.milestones_done,
                    milestonesTotal: plan.milestones_total,
                  })}
                </p>
              </li>
            ))}
          </ul>
        )}
        {facts.learning_plans_truncated ? (
          <p className="text-sm text-muted-foreground">
            {t('progressReports.facts.plans.truncated', {
              max: FACT_PLANS_MAX,
            })}
          </p>
        ) : null}
      </Block>
    </section>
  );
}
