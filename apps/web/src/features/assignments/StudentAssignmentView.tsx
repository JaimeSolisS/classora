import { useEffect, useId, useRef, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { BADGE_CLASS } from '@/features/academics/styles';
import { useFormatters } from '@/lib/formatters';
import type { StudentAssignment, SubmissionPut } from './api';
import { useDeadlineText } from './DeadlineText';
import { SafeLink } from './SafeLink';
import { LateMarker, StateBadge } from './StateBadge';
import { SubmitForm } from './SubmitForm';

// One student's homework (#313 E4, D2), shared by the family page and the
// staff and tutor page: the title (h1), subject, "Set by {creator}", the
// deadline text, "Max score {max}" / "Feedback only" and the instructions;
// the work ("Your work" for the student, "{child}'s work" for a guardian,
// "Submission" for staff): the state badge, "Late", the text, the link (C6),
// "Handed in on {instant}" and "Handed in to the tutor"; the grade (score of
// max and percent, feedback, "Updated"). Every text is plain text.
//
// The student's own login gets the hand-in form when `can_submit` says so
// (E6); a guardian or staff never does, whatever the flags say (E7). Once
// the deadline passed with work handed in it says "Handing in closed on
// {deadline}.". After a hand-in, "Work handed in." / "Work replaced." shows
// and focus moves to the "Your work" heading.

export type ViewAudience = 'self' | 'guardian' | 'staff';

export interface StudentAssignmentViewProps {
  item: StudentAssignment;
  audience: ViewAudience;
  /** The child's name (the guardian's heading). */
  studentName?: string | null;
  /** The student's hand-in; resolves with the stored item. */
  onSubmit?: (body: SubmissionPut) => Promise<void>;
  /** Refetches and remounts (it then focuses its heading). */
  onReload?: () => void;
  /** Focus the heading when it mounts (after a Reload). */
  focusHeadingOnMount?: boolean;
  /** Read `now` (tests). */
  now?: () => number;
}

const MULTILINE = 'min-w-0 text-sm break-words whitespace-pre-line';

export function StudentAssignmentView({
  item,
  audience,
  studentName = null,
  onSubmit,
  onReload,
  focusHeadingOnMount = false,
  now = Date.now,
}: StudentAssignmentViewProps) {
  const { t } = useTranslation();
  const { formatInstant } = useFormatters();
  const deadlineText = useDeadlineText(now);
  const id = useId();
  const [notice, setNotice] = useState<string | null>(null);
  const headingRef = useRef<HTMLHeadingElement>(null);
  const workRef = useRef<HTMLHeadingElement>(null);
  const [focus, setFocus] = useState<{
    to: 'heading' | 'work';
    n: number;
  } | null>(focusHeadingOnMount ? { to: 'heading', n: 0 } : null);

  useEffect(() => {
    if (!focus) {
      return;
    }
    // After the render that shows the stored work (0 ms: past the commit).
    const timer = window.setTimeout(() => {
      (focus.to === 'work' ? workRef.current : headingRef.current)?.focus();
    }, 0);
    return () => window.clearTimeout(timer);
  }, [focus]);

  const { submission } = item;
  const self = audience === 'self';
  const canSubmit = self && item.can_submit && onSubmit !== undefined;
  const handedIn = submission.submitted_at !== null;
  const deadlinePassed = Date.parse(item.deadline_at) < now();
  const workHeading = self
    ? t('assignments.student.yourWork')
    : audience === 'guardian'
      ? studentName
        ? t('assignments.student.childWork', { name: studentName })
        : t('assignments.student.submission')
      : t('assignments.student.submission');

  return (
    <div className="flex w-full max-w-3xl min-w-0 flex-col gap-6">
      <div className="flex min-w-0 flex-col gap-3">
        <h1
          ref={headingRef}
          tabIndex={-1}
          className="text-2xl font-semibold break-words outline-none"
        >
          {item.title}
        </h1>
        <p className="flex min-w-0 flex-wrap gap-x-3 gap-y-1 text-sm">
          <span className="break-words">{item.subject.name}</span>
          <span className="break-words">
            {t('assignments.setBy', { name: item.creator.display_name })}
          </span>
        </p>
        <p className="flex min-w-0 flex-wrap gap-x-3 gap-y-1 text-sm">
          <span>
            {deadlineText({
              ...item,
              state: submission.state,
              is_late: submission.is_late,
            })}
          </span>
          <span>
            {item.max_score !== null
              ? t('assignments.maxScore', { max: item.max_score })
              : t('assignments.feedbackOnly')}
          </span>
        </p>
        <p className={MULTILINE}>{item.instructions}</p>
      </div>

      {notice ? (
        <p role="status" className="text-sm font-medium">
          {notice}
        </p>
      ) : null}

      <section
        aria-labelledby={`${id}-work`}
        className="flex min-w-0 flex-col gap-3 rounded-md border border-border p-4"
      >
        <h2
          id={`${id}-work`}
          ref={workRef}
          tabIndex={-1}
          className="text-lg font-semibold break-words outline-none"
        >
          {workHeading}
        </h2>
        <p className="flex flex-wrap items-center gap-2">
          <StateBadge state={submission.state} />
          <LateMarker late={submission.is_late} />
        </p>
        {handedIn ? (
          <div className="flex min-w-0 flex-col gap-1 text-sm">
            <p>
              {t('assignments.handedInOn', {
                when: formatInstant(submission.submitted_at!),
              })}
              {submission.submitted_via === 'tutor'
                ? ` · ${t('assignments.student.toTutor')}`
                : ''}
            </p>
            {submission.text ? (
              <p className={MULTILINE}>{submission.text}</p>
            ) : null}
            {submission.url ? (
              <p className="min-w-0">
                <SafeLink url={submission.url} />
              </p>
            ) : null}
          </div>
        ) : null}
        {canSubmit ? (
          <SubmitForm
            key={submission.version}
            item={item}
            onReload={() => onReload?.()}
            onSubmit={async (body) => {
              await onSubmit(body);
              setNotice(
                handedIn
                  ? t('assignments.notices.replaced')
                  : t('assignments.notices.handedIn'),
              );
              setFocus((current) => ({ to: 'work', n: (current?.n ?? 0) + 1 }));
            }}
          />
        ) : self &&
          handedIn &&
          submission.state !== 'graded' &&
          deadlinePassed ? (
          <p className="text-sm">
            {t('assignments.student.closed', {
              deadline: formatInstant(item.deadline_at),
            })}
          </p>
        ) : null}
      </section>

      {submission.state === 'graded' ? (
        <section
          aria-labelledby={`${id}-grade`}
          className="flex min-w-0 flex-col gap-2 rounded-md border border-border p-4 text-sm"
        >
          <h2 id={`${id}-grade`} className="text-lg font-semibold">
            {t('assignments.student.grade')}
          </h2>
          {submission.score !== null && item.max_score !== null ? (
            <p className="font-medium">
              {t('assignments.scoreWithPercent', {
                score: submission.score,
                max: item.max_score,
                percent: submission.score_percent,
              })}
            </p>
          ) : null}
          {submission.feedback ? (
            <p className={MULTILINE}>{submission.feedback}</p>
          ) : null}
          <p className="flex flex-wrap items-center gap-2 text-muted-foreground">
            {submission.graded_at ? (
              <span>
                {t('assignments.gradedOn', {
                  date: formatInstant(submission.graded_at, 'date'),
                })}
              </span>
            ) : null}
            {submission.updated ? (
              <span className={BADGE_CLASS}>{t('assignments.updated')}</span>
            ) : null}
          </p>
        </section>
      ) : null}
    </div>
  );
}
