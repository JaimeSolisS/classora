import { useQuery, useQueryClient } from '@tanstack/react-query';
import { useId, type ReactNode } from 'react';
import { useTranslation } from 'react-i18next';
import { Link, useSearchParams } from 'react-router';
import { ErrorAlert } from '@/components/feedback/ErrorAlert';
import { LoadingState } from '@/components/feedback/LoadingState';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { FieldError } from '@/features/auth/FieldError';
import { ConsentRequired } from '@/features/my-schedule/ConsentRequired';
import { SELECT_CLASS } from '@/features/students/styles';
import { useFormatters } from '@/lib/formatters';
import type { LocalDate } from '@/lib/time';
import { evaluationPath } from '../evaluations/api';
import { BADGE_CLASS, SMALL_LINK_CLASS } from '../styles';
import {
  fetchAcademicHistory,
  historyKey,
  HISTORY_KEY,
  type AcademicHistory as History,
  type HistoryEntry,
  type HistoryQuery,
} from './api';
import {
  GROUPS,
  hasHistoryFilters,
  historyQuery,
  historyRangeInvalid,
  parseHistoryControls,
  withHistoryControls,
  type HistoryControls,
} from './urlState';
import {
  historyState,
  knownEntries,
  subjectsOf,
  type HistoryState,
  type SubjectChoice,
} from './historyState';

// The academic history of one student (#308 E), shared by the staff and
// tutor page and the family page. Its controls live in the URL: "Group by"
// (school year, semester, term, no grouping), "Subject" (the subjects of
// the cached unfiltered history, no extra request), From and To (To before
// From is a field error and nothing is read) and the "Grouped /
// Chronological" toggle (aria-pressed). Grouped: a section per period in
// the API's order (h2 with its dates, or "No period"), an h3 per subject
// with "{count} evaluations · average {average} %", then its entries.
// Chronological: every entry, newest first, each naming its subject. An
// entry is an article with the template, the kind, the date, the author,
// "Updated", the course, the total, every criterion and the feedback, all
// plain text with the API's numbers as sent (no client arithmetic, no
// colour scale); staff and tutors also get "Details" (the evaluation
// page). An entry kind this view doesn't know is skipped. States: loading,
// error with "Try again", the truncated notice, empty, empty with filters,
// consent required (403 student_consent_required) and "This academic
// history isn't available." (404).

export type Audience = 'staff' | 'family';

export interface AcademicHistoryViewProps {
  state: HistoryState;
  controls: HistoryControls;
  onControls: (next: HistoryControls) => void;
  subjects: readonly SubjectChoice[];
  audience: Audience;
  /** The student's name, for the consent message. */
  studentName?: string;
  /** The server refused the range (422 range_invalid). */
  rangeError?: boolean;
}

const NO_FILTERS = { subject: null, from: null, to: null } as const;

function Heading({
  level,
  id,
  className,
  children,
}: {
  level: 2 | 3 | 4;
  id?: string;
  className?: string;
  children: ReactNode;
}) {
  const Tag = `h${level}` as const;
  return (
    <Tag id={id} className={className}>
      {children}
    </Tag>
  );
}

export function HistoryEntryCard({
  entry,
  level,
  showSubject,
  showDetails,
}: {
  entry: HistoryEntry;
  level: 2 | 3 | 4;
  showSubject: boolean;
  showDetails: boolean;
}) {
  const { t } = useTranslation();
  const { formatLocalDate } = useFormatters();
  const id = useId();
  const date = formatLocalDate(entry.evaluated_on);
  return (
    <article
      aria-labelledby={id}
      className="flex min-w-0 flex-col gap-2 rounded-lg border border-border p-3"
    >
      <Heading
        level={level}
        id={id}
        className="text-base font-semibold break-words"
      >
        {entry.template_name}
      </Heading>
      <p className="text-sm break-words">
        {showSubject
          ? t('academics.history.entry.metaWithSubject', {
              subject: entry.subject.name,
              kind: t(`academics.kinds.${entry.evaluation_kind}`),
              date,
            })
          : t('academics.history.entry.meta', {
              kind: t(`academics.kinds.${entry.evaluation_kind}`),
              date,
            })}
      </p>
      <div className="flex flex-wrap items-center gap-2 text-sm">
        <span className="break-words">
          {t('academics.history.entry.by', { name: entry.author.display_name })}
        </span>
        {entry.updated ? (
          <span className={BADGE_CLASS}>
            {t('academics.history.entry.updated')}
          </span>
        ) : null}
      </div>
      {entry.course ? (
        <p className="text-sm break-words">
          {t('academics.history.entry.course', {
            assessment: entry.course.assessment_title,
            cohort: entry.course.cohort_name,
          })}
        </p>
      ) : null}
      <p className="text-sm font-semibold">
        {t('academics.history.entry.total', {
          total: entry.score_total,
          max: entry.max_total,
          percent: entry.score_percent,
        })}
      </p>
      <ul className="flex min-w-0 flex-col gap-2 border-l-2 border-border pl-3">
        {entry.criteria.map((criterion, index) => (
          <li key={index} className="flex min-w-0 flex-col text-sm">
            <span className="break-words">
              <span className="font-medium">{criterion.title}</span>
              {': '}
              {t('academics.history.entry.score', {
                score: criterion.score,
                max: criterion.max_score,
              })}
            </span>
            {criterion.comment ? (
              <span className="break-words whitespace-pre-line text-muted-foreground">
                {criterion.comment}
              </span>
            ) : null}
          </li>
        ))}
      </ul>
      {entry.feedback ? (
        <div className="flex min-w-0 flex-col gap-1">
          <p className="text-sm font-medium">
            {t('academics.history.entry.feedback')}
          </p>
          <p className="text-sm break-words whitespace-pre-line">
            {entry.feedback}
          </p>
        </div>
      ) : null}
      {showDetails ? (
        <Link
          to={evaluationPath(entry.id)}
          className={`${SMALL_LINK_CLASS} self-start`}
          aria-label={t('academics.history.entry.detailsOf', {
            name: entry.template_name,
            date,
          })}
        >
          {t('academics.history.entry.details')}
        </Link>
      ) : null}
    </article>
  );
}

function DateField({
  label,
  value,
  onChange,
  error,
}: {
  label: string;
  value: LocalDate | null;
  onChange: (value: LocalDate | null) => void;
  error?: string;
}) {
  const id = useId();
  return (
    <div className="flex min-w-0 flex-col gap-2">
      <label htmlFor={id} className="text-sm font-medium">
        {label}
      </label>
      <Input
        id={id}
        type="date"
        value={value ?? ''}
        aria-invalid={error ? true : undefined}
        aria-describedby={error ? `${id}-error` : undefined}
        onChange={(event) =>
          onChange(
            /^\d{4}-\d{2}-\d{2}$/.test(event.target.value)
              ? (event.target.value as LocalDate)
              : null,
          )
        }
      />
      <FieldError id={`${id}-error`} message={error} />
    </div>
  );
}

function Grouped({
  history,
  showDetails,
}: {
  history: History;
  showDetails: boolean;
}) {
  const { t } = useTranslation();
  const { formatLocalDate } = useFormatters();
  const id = useId();
  const byId = new Map(knownEntries(history).map((entry) => [entry.id, entry]));
  const unnamed = history.group_by === 'none';
  return (
    <div className="flex min-w-0 flex-col gap-6">
      {history.groups.map((group, index) => {
        const headingId = `${id}-g${index}`;
        const subjectLevel = unnamed ? 2 : 3;
        // #82: a subject with homework grades only has no evaluation to
        // show here (#313 renders those); a period without any is skipped.
        const subjects = group.subjects.filter((subject) => subject.count > 0);
        if (subjects.length === 0) return null;
        return (
          <section
            key={group.period?.id ?? `none-${index}`}
            aria-labelledby={unnamed ? undefined : headingId}
            aria-label={
              unnamed ? t('academics.history.allEvaluations') : undefined
            }
            className="flex min-w-0 flex-col gap-4"
          >
            {unnamed ? null : (
              <h2 id={headingId} className="text-xl font-semibold break-words">
                {group.period
                  ? t('academics.history.period', {
                      name: group.period.name,
                      start: formatLocalDate(group.period.starts_on),
                      end: formatLocalDate(group.period.ends_on),
                    })
                  : t('academics.history.noPeriod')}
              </h2>
            )}
            {subjects.map((subject) => {
              const entries = subject.entry_ids
                .map((entryId) => byId.get(entryId))
                .filter((entry): entry is HistoryEntry => entry !== undefined);
              return (
                <div
                  key={subject.subject.id}
                  className="flex min-w-0 flex-col gap-3"
                >
                  <div className="flex min-w-0 flex-col gap-1">
                    <Heading
                      level={subjectLevel}
                      className="text-lg font-semibold break-words"
                    >
                      {subject.subject.name}
                    </Heading>
                    <p className="text-sm text-muted-foreground">
                      {t('academics.history.subjectSummary', {
                        count: subject.count,
                        average: subject.average_percent,
                      })}
                    </p>
                  </div>
                  <ul className="flex min-w-0 flex-col gap-3">
                    {entries.map((entry) => (
                      <li key={entry.id} className="min-w-0">
                        <HistoryEntryCard
                          entry={entry}
                          level={unnamed ? 3 : 4}
                          showSubject={false}
                          showDetails={showDetails}
                        />
                      </li>
                    ))}
                  </ul>
                </div>
              );
            })}
          </section>
        );
      })}
    </div>
  );
}

export function AcademicHistoryView({
  state,
  controls,
  onControls,
  subjects,
  audience,
  studentName = '',
  rangeError = false,
}: AcademicHistoryViewProps) {
  const { t } = useTranslation();
  const id = useId();
  const filtered = hasHistoryFilters(controls);
  const invalidRange = rangeError || historyRangeInvalid(controls);
  const showDetails = audience === 'staff';
  const change = (next: Partial<HistoryControls>) =>
    onControls({ ...controls, ...next });
  const clear = (
    <Button
      type="button"
      variant="outline"
      size="sm"
      onClick={() => change(NO_FILTERS)}
    >
      {t('academics.history.clearFilters')}
    </Button>
  );
  const choices =
    controls.subject && !subjects.some((s) => s.id === controls.subject)
      ? [
          ...subjects,
          {
            id: controls.subject,
            name: t('academics.history.selectedSubject'),
          },
        ]
      : subjects;

  let content: ReactNode;
  if (state.status === 'loading') {
    content = <LoadingState rows={4} />;
  } else if (state.status === 'invalid') {
    content = null;
  } else if (state.status === 'error') {
    content = <ErrorAlert error={state.error} onRetry={state.onRetry} />;
  } else if (state.status === 'consent') {
    content = (
      <ConsentRequired
        name={studentName}
        title={t('academics.history.consentTitle')}
      />
    );
  } else if (state.status === 'missing') {
    content = (
      <p role="status" className="text-sm">
        {t('academics.history.unavailable')}
      </p>
    );
  } else {
    const { history } = state;
    const entries = knownEntries(history);
    content = (
      <div className="flex min-w-0 flex-col gap-4">
        {history.truncated ? (
          <p
            role="status"
            className="rounded-md border border-border p-3 text-sm"
          >
            {t('academics.history.truncated')}
          </p>
        ) : null}
        {entries.length === 0 ? (
          <div className="flex flex-col items-start gap-2">
            <p role="status" className="text-sm">
              {filtered
                ? t('academics.history.emptyFiltered')
                : audience === 'family'
                  ? t('academics.history.emptyFamily')
                  : t('academics.history.empty')}
            </p>
            {filtered ? clear : null}
          </div>
        ) : controls.view === 'list' ? (
          <ul className="flex min-w-0 flex-col gap-3">
            {entries.map((entry) => (
              <li key={entry.id} className="min-w-0">
                <HistoryEntryCard
                  entry={entry}
                  level={2}
                  showSubject
                  showDetails={showDetails}
                />
              </li>
            ))}
          </ul>
        ) : (
          <Grouped history={history} showDetails={showDetails} />
        )}
      </div>
    );
  }

  return (
    <div className="flex min-w-0 flex-col gap-6">
      <section
        aria-label={t('academics.history.controls')}
        className="flex min-w-0 flex-col gap-4 rounded-lg border border-border p-4"
      >
        <div className="grid min-w-0 grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-4">
          <div className="flex min-w-0 flex-col gap-2">
            <label htmlFor={`${id}-group`} className="text-sm font-medium">
              {t('academics.history.groupBy')}
            </label>
            <select
              id={`${id}-group`}
              className={SELECT_CLASS}
              value={controls.group}
              onChange={(event) =>
                change({
                  group:
                    GROUPS.find((group) => group === event.target.value) ??
                    'school_year',
                })
              }
            >
              {GROUPS.map((group) => (
                <option key={group} value={group}>
                  {t(`academics.history.groups.${group}`)}
                </option>
              ))}
            </select>
          </div>
          <div className="flex min-w-0 flex-col gap-2">
            <label htmlFor={`${id}-subject`} className="text-sm font-medium">
              {t('academics.history.subject')}
            </label>
            <select
              id={`${id}-subject`}
              className={SELECT_CLASS}
              value={controls.subject ?? ''}
              onChange={(event) =>
                change({ subject: event.target.value || null })
              }
            >
              <option value="">{t('academics.history.allSubjects')}</option>
              {choices.map((subject) => (
                <option key={subject.id} value={subject.id}>
                  {subject.name}
                </option>
              ))}
            </select>
          </div>
          <DateField
            label={t('academics.history.from')}
            value={controls.from}
            onChange={(from) => change({ from })}
          />
          <DateField
            label={t('academics.history.to')}
            value={controls.to}
            onChange={(to) => change({ to })}
            error={
              invalidRange ? t('academics.history.rangeInvalid') : undefined
            }
          />
        </div>
        <div className="flex flex-wrap items-center gap-3">
          <div
            role="group"
            aria-label={t('academics.history.view')}
            className="flex flex-wrap gap-1"
          >
            {(['grouped', 'list'] as const).map((view) => (
              <Button
                key={view}
                type="button"
                size="sm"
                variant={controls.view === view ? 'default' : 'outline'}
                aria-pressed={controls.view === view}
                onClick={() => change({ view })}
              >
                {t(`academics.history.views.${view}`)}
              </Button>
            ))}
          </div>
          {filtered ? clear : null}
        </div>
      </section>
      {content}
    </div>
  );
}

/**
 * The history of `studentId` with its controls in the URL (E1). The subject
 * options come from cached unfiltered reads for the same student and range
 * (no extra request), plus the current response's.
 */
export function AcademicHistory({
  studentId,
  audience,
  studentName,
}: {
  studentId: string;
  audience: Audience;
  studentName?: string;
}) {
  const [searchParams, setSearchParams] = useSearchParams();
  const client = useQueryClient();
  const controls = parseHistoryControls(searchParams);
  const invalid = historyRangeInvalid(controls);
  const query = historyQuery(controls);
  const history = useQuery({
    queryKey: historyKey(studentId, query),
    queryFn: ({ signal }) => fetchAcademicHistory(studentId, query, signal),
    enabled: !invalid && studentId !== '',
    retry: false,
  });
  const { state, rangeError } = historyState(invalid, history);

  const cached = client
    .getQueriesData<History>({ queryKey: [...HISTORY_KEY, studentId] })
    .filter(([key]) => {
      const keyQuery = key[3] as HistoryQuery | undefined;
      return (
        keyQuery !== undefined &&
        keyQuery.subject === null &&
        keyQuery.from === query.from &&
        keyQuery.to === query.to
      );
    })
    .map(([, data]) => data);
  const subjects = new Map<string, SubjectChoice>();
  for (const source of [...cached, history.data]) {
    for (const subject of subjectsOf(source)) {
      subjects.set(subject.id, subject);
    }
  }

  return (
    <AcademicHistoryView
      state={state}
      controls={controls}
      onControls={(next) =>
        setSearchParams(withHistoryControls(searchParams, next))
      }
      subjects={[...subjects.values()].sort((a, b) =>
        a.name.localeCompare(b.name, undefined, { sensitivity: 'base' }),
      )}
      audience={audience}
      studentName={studentName}
      rangeError={rangeError}
    />
  );
}
