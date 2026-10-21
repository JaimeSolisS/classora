import { useId } from 'react';
import { useTranslation } from 'react-i18next';
import { ErrorAlert } from '@/components/feedback/ErrorAlert';
import { Spinner } from '@/components/feedback/Spinner';
import { Button } from '@/components/ui/button';
import type { TutorSuggestion, TutorSuggestionList } from './api';
import { hoursAndMinutes } from './rules';

// "Suggested tutors" (#51 G2): the tutors qualified, available and free for
// the form's slot, in the server's order (the lightest week first; ties by
// the day, then those preferring the room). Each shows its minutes this
// week and that day, the teaching limits when set (shown only) and a
// "Prefers this room" badge, with a "Choose" button that hands the tutor to
// the form (it sets the Tutor picker and never submits). Loading, empty and
// error states; the error is the shared ErrorAlert (a 422 goes onto the
// form's fields instead).

export type TutorSuggestionsState =
  | { status: 'loading' }
  | { status: 'loaded'; result: TutorSuggestionList }
  | { status: 'error'; error: unknown };

export interface TutorSuggestionsProps {
  state: TutorSuggestionsState;
  onChoose: (suggestion: TutorSuggestion) => void;
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

export function TutorSuggestions({ state, onChoose }: TutorSuggestionsProps) {
  const { t } = useTranslation();
  const duration = useDuration();
  const id = useId();
  const headingId = `${id}-heading`;

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
    content = (
      <p role="status" className="text-sm">
        {t('scheduling.tutorSuggestions.empty')}
      </p>
    );
  } else {
    const { items, eligible_count: count } = state.result;
    content = (
      <>
        <p role="status" className="text-sm text-muted-foreground">
          {t('scheduling.tutorSuggestions.shown', {
            count,
            shown: items.length,
          })}
        </p>
        <ol className="flex flex-col gap-2">
          {items.map((item) => {
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
                </div>
                <div>
                  <Button
                    type="button"
                    variant="outline"
                    aria-label={t('scheduling.tutorSuggestions.chooseTutor', {
                      name: item.tutor.display_name,
                    })}
                    onClick={() => onChoose(item)}
                  >
                    {t('scheduling.tutorSuggestions.choose')}
                  </Button>
                </div>
              </li>
            );
          })}
        </ol>
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
