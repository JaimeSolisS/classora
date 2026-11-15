import { useId, useState, type ReactNode } from 'react';
import { useTranslation } from 'react-i18next';
import { ErrorAlert } from '@/components/feedback/ErrorAlert';
import { Spinner } from '@/components/feedback/Spinner';
import { Button } from '@/components/ui/button';
import type { TutorSuggestion } from './api';
import {
  hoursAndMinutes,
  suggestionEmptyState,
  TUTOR_SUGGESTIONS_SHOWN,
  type AnyTutorSuggestionList,
} from './rules';
import { useWarningMessage } from './warningMessages';

// "Suggested tutors" (#51 G2): the tutors qualified, available and free for
// the slot, in the server's order (the lightest week first; ties by the
// day, then those preferring the room). Each shows its minutes this week
// and that day, the teaching limits when set (shown only) and a "Prefers
// this room" badge, with a "Choose" button that hands the tutor to the
// caller (it never submits). Loading, empty and error states; the error is
// the shared ErrorAlert (a 422 goes onto the form's fields instead).
//
// HF-05b B3-B5: each row also lists the workload warnings a create would
// return (as text) or, for a one-off slot with a limit set and no warning,
// "Within their limits". An empty list says why from the counts: nobody
// holds the subject (with `emptyLink`, the way to assign it), or how many
// are busy or unavailable (a series: in at least one session; no session
// left to check). Without the counts or `subjectName`, the general text.
//
// The scheduling form (HF-05b B3, B4) passes `chosenId`: the chosen tutor's
// row shows "Chosen" (aria-pressed) and the list stays; `summary`, the line
// that says what every listed tutor meets; and `collapse`: the first 5 rows
// plus "View all".

export type TutorSuggestionsState =
  | { status: 'loading' }
  | { status: 'loaded'; result: AnyTutorSuggestionList }
  | { status: 'error'; error: unknown };

export interface TutorSuggestionsProps {
  state: TutorSuggestionsState;
  onChoose: (suggestion: TutorSuggestion) => void;
  /** The chosen tutor (the form); none: "Choose" buttons only. */
  chosenId?: string;
  /** What every listed tutor meets (the form). */
  summary?: string;
  /** The subject's name, for the empty-state text. */
  subjectName?: string;
  /** The link to assign subjects (qualification.review holders). */
  emptyLink?: ReactNode;
  /** A series: no "Within their limits" (its warnings are always empty). */
  series?: boolean;
  /** Show the first rows and "View all". */
  collapse?: boolean;
}

function useDuration(): (minutes: number) => string {
  const { t } = useTranslation();
  return (total) => {
    const { hours, minutes } = hoursAndMinutes(total);
    if (hours === 0) {
      return t('scheduling.tutorSuggestions.minutes', { minutes });
    }
    if (minutes === 0) {
      return t('scheduling.tutorSuggestions.hours', { hours });
    }
    return t('scheduling.tutorSuggestions.hoursMinutes', { hours, minutes });
  };
}

/** The empty-state text from the counts (B5). */
function useEmptyText(): (
  result: AnyTutorSuggestionList,
  subject: string | undefined,
) => string {
  const { t } = useTranslation();
  return (result, subject) => {
    const empty = suggestionEmptyState(result);
    if (empty.kind === 'noOccurrences') {
      return t('scheduling.tutorSuggestions.noOccurrences');
    }
    if (empty.kind === 'unknown' || subject === undefined) {
      return t('scheduling.tutorSuggestions.empty');
    }
    if (empty.kind === 'noneQualified') {
      return t('scheduling.tutorSuggestions.noneQualified', { subject });
    }
    const reasons = [
      empty.busy > 0
        ? t('scheduling.tutorSuggestions.busyPart', { count: empty.busy })
        : null,
      empty.unavailable > 0
        ? t('scheduling.tutorSuggestions.unavailablePart', {
            count: empty.unavailable,
          })
        : null,
    ]
      .filter((part): part is string => part !== null)
      .join(t('scheduling.tutorSuggestions.and'));
    const text =
      reasons === ''
        ? t('scheduling.tutorSuggestions.noneFreeNoReason', {
            count: empty.qualified,
            subject,
          })
        : t('scheduling.tutorSuggestions.noneFree', {
            count: empty.qualified,
            subject,
            reasons,
          });
    return empty.occurrences === null
      ? `${text}.`
      : t('scheduling.tutorSuggestions.inSeries', {
          text,
          count: empty.occurrences,
        });
  };
}

export function TutorSuggestions({
  state,
  onChoose,
  chosenId,
  summary,
  subjectName,
  emptyLink,
  series = false,
  collapse = false,
}: TutorSuggestionsProps) {
  const { t } = useTranslation();
  const duration = useDuration();
  const emptyText = useEmptyText();
  const warningMessage = useWarningMessage();
  const id = useId();
  const headingId = `${id}-heading`;
  const [expanded, setExpanded] = useState(false);

  let content;
  if (state.status === 'loading') {
    content = (
      <p role="status" className="flex items-center gap-2 text-sm">
        <Spinner />
        {t('scheduling.tutorSuggestions.loading')}
      </p>
    );
  } else if (state.status === 'error') {
    content = <ErrorAlert error={state.error} />;
  } else if (state.result.items.length === 0) {
    const noneQualified =
      suggestionEmptyState(state.result).kind === 'noneQualified';
    content = (
      <div className="flex flex-col gap-2">
        <p role="status" className="text-sm break-words">
          {emptyText(state.result, subjectName)}
        </p>
        {noneQualified && subjectName !== undefined && emptyLink ? (
          <p className="text-sm">{emptyLink}</p>
        ) : null}
      </div>
    );
  } else {
    const { items, eligible_count: count } = state.result;
    const shownItems =
      collapse && !expanded ? items.slice(0, TUTOR_SUGGESTIONS_SHOWN) : items;
    content = (
      <>
        {summary ? <p className="text-sm break-words">{summary}</p> : null}
        <p role="status" className="text-sm text-muted-foreground">
          {t('scheduling.tutorSuggestions.shown', {
            count,
            shown: items.length,
          })}
        </p>
        <ol className="flex flex-col gap-2">
          {shownItems.map((item) => {
            const limits = [
              item.weekly_limit_minutes === null
                ? null
                : t('scheduling.tutorSuggestions.weeklyLimit', {
                    duration: duration(item.weekly_limit_minutes),
                  }),
              item.daily_limit_minutes === null
                ? null
                : t('scheduling.tutorSuggestions.dailyLimit', {
                    duration: duration(item.daily_limit_minutes),
                  }),
            ].filter((text): text is string => text !== null);
            const warnings = item.warnings ?? [];
            const chosen = chosenId !== undefined && chosenId === item.tutor.id;
            return (
              <li
                key={item.tutor.id}
                className="flex flex-col gap-2 rounded-md border border-border p-3 sm:flex-row sm:items-center sm:justify-between"
              >
                <div className="flex min-w-0 flex-col gap-1">
                  <p className="font-medium break-words">
                    {item.tutor.display_name}
                    {item.prefers_classroom ? (
                      <span className="ml-2 inline-block rounded-sm border border-border px-1.5 py-0.5 text-xs font-normal">
                        {t('scheduling.tutorSuggestions.prefersRoom')}
                      </span>
                    ) : null}
                  </p>
                  <p className="text-sm break-words text-muted-foreground">
                    {t('scheduling.tutorSuggestions.workload', {
                      week: duration(item.weekly_minutes),
                      day: duration(item.daily_minutes),
                    })}
                    {limits.length > 0 ? ` · ${limits.join(' · ')}` : null}
                  </p>
                  {warnings.length > 0 ? (
                    <ul className="flex flex-col gap-1 border-s-4 border-chart-2 ps-2 text-sm break-words">
                      {warnings.map((warning, position) => (
                        <li key={`${warning.code}-${position}`}>
                          {warningMessage(warning)}
                        </li>
                      ))}
                    </ul>
                  ) : !series && limits.length > 0 ? (
                    <p className="text-sm">
                      {t('scheduling.tutorSuggestions.withinLimits')}
                    </p>
                  ) : null}
                </div>
                <div>
                  <Button
                    type="button"
                    variant={chosen ? 'default' : 'outline'}
                    aria-pressed={chosenId === undefined ? undefined : chosen}
                    aria-label={
                      chosen
                        ? t('scheduling.tutorSuggestions.chosenTutor', {
                            name: item.tutor.display_name,
                          })
                        : t('scheduling.tutorSuggestions.chooseTutor', {
                            name: item.tutor.display_name,
                          })
                    }
                    onClick={() => {
                      if (!chosen) {
                        onChoose(item);
                      }
                    }}
                  >
                    {chosen
                      ? t('scheduling.tutorSuggestions.chosenLabel')
                      : t('scheduling.tutorSuggestions.choose')}
                  </Button>
                </div>
              </li>
            );
          })}
        </ol>
        {shownItems.length < items.length ? (
          <div>
            <Button
              type="button"
              variant="outline"
              onClick={() => setExpanded(true)}
            >
              {t('scheduling.tutorSuggestions.viewAll', {
                count: items.length,
              })}
            </Button>
          </div>
        ) : null}
      </>
    );
  }

  return (
    <section
      aria-labelledby={headingId}
      className="flex flex-col gap-3 rounded-md border border-border p-4"
    >
      <h2 id={headingId} className="font-medium">
        {t('scheduling.tutorSuggestions.heading')}
      </h2>
      <p className="text-sm text-muted-foreground">
        {t('scheduling.tutorSuggestions.intro')}
      </p>
      {content}
    </section>
  );
}
