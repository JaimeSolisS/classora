import { useEffect, useId, useRef, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { Link } from 'react-router';
import { Button } from '@/components/ui/button';
import { ConfirmDialog } from '@/features/academics/evaluations/ConfirmDialog';
import { BADGE_CLASS } from '@/features/academics/styles';
import { LINK_BUTTON_CLASS } from '@/features/students/styles';
import { useFormatters } from '@/lib/formatters';
import {
  editAssignmentPath,
  type AssignmentView,
  type GradePut,
  type RosterEntry,
  type SubmissionPut,
} from './api';
import { AssignmentFailure } from './AssignmentFailure';
import { useCountsText } from './Counts';
import { useDueText } from './DeadlineText';
import { GradeDialog } from './GradeDialog';
import { RecordSubmissionDialog } from './RecordSubmissionDialog';
import { classifyRosterRefusal } from './rules';
import { SafeLink } from './SafeLink';
import { LateMarker, StateBadge } from './StateBadge';

// One homework with its roster (#313 C2-C5), for the creator and
// assignment.review holders: the title (h1), subject, deadline, "Max score
// {max}" or "Feedback only", the linked class's date, "Set by {creator}",
// the instructions and the counts; then one card per student in the API's
// order: the state badge, "Late", the handed-in work ("Recorded by the
// tutor", the text, the link) and the grade (score, feedback, "Updated",
// the internal change reason). Every text is plain text.
//
// Actions strictly by the API's flags (no flag, no control): Edit
// (can_edit), Delete (can_delete; a confirmation), per row "Record
// handed-in work" (can_record_submission, nothing handed in yet) and
// "Grade" / "Change grade" (can_grade, work handed in).
//
// Focus: Cancel or Escape in a dialog returns it to the button that opened
// it; a saved grade returns it to the row's grade button ("Grade saved."
// shows in a status, C5); recorded work focuses its status message once
// the roster is read again; a refused delete focuses its failure region.

export type RosterDialog =
  | { kind: 'delete' }
  | { kind: 'grade'; studentId: string }
  | { kind: 'record'; studentId: string };

export interface RosterActions {
  /** A grade; resolves when stored (the page replaces the row). */
  onGrade: (row: RosterEntry, body: GradePut) => Promise<void>;
  /** Handed-in work; resolves once the roster was read again. */
  onRecord: (row: RosterEntry, body: SubmissionPut) => Promise<void>;
  /** Deletes; the page navigates away. */
  onDelete: () => Promise<void>;
  /** Refetches and remounts the view (it then focuses its heading). */
  onReload: () => void;
}

export interface RosterViewProps {
  assignment: AssignmentView;
  actions?: RosterActions;
  /** A notice to show first, focused (after a create or an edit). */
  initialNotice?: string | null;
  /** Focus the heading when it mounts (after a Reload). */
  focusHeadingOnMount?: boolean;
  /** Stories: open a dialog. */
  initialDialog?: RosterDialog | null;
}

type FocusTarget =
  | { to: 'notice' | 'failure' | 'trigger' | 'heading' }
  | { to: 'grade'; studentId: string };

const MULTILINE = 'min-w-0 text-sm break-words whitespace-pre-line';

export function RosterView({
  assignment,
  actions,
  initialNotice = null,
  focusHeadingOnMount = false,
  initialDialog = null,
}: RosterViewProps) {
  const { t } = useTranslation();
  const { formatInstant } = useFormatters();
  const dueText = useDueText();
  const countsText = useCountsText();
  const id = useId();
  const [notice, setNotice] = useState<string | null>(initialNotice);
  const [failure, setFailure] = useState<unknown>(null);
  const [dialog, setDialog] = useState<RosterDialog | null>(
    actions ? initialDialog : null,
  );
  const trigger = useRef<HTMLElement | null>(null);
  const headingRef = useRef<HTMLHeadingElement>(null);
  const noticeRef = useRef<HTMLParagraphElement>(null);
  const failureRef = useRef<HTMLDivElement>(null);
  const rowsRef = useRef<HTMLUListElement>(null);
  const [focusTarget, setFocusTarget] = useState<
    (FocusTarget & { n: number }) | null
  >(
    initialNotice
      ? { n: 0, to: 'notice' }
      : focusHeadingOnMount
        ? { n: 0, to: 'heading' }
        : null,
  );

  function focusNext(target: FocusTarget) {
    setFocusTarget((current) => ({ ...target, n: (current?.n ?? 0) + 1 }));
  }

  useEffect(() => {
    if (!focusTarget) {
      return;
    }
    // After the render that shows the target (0 ms: past React's commit).
    const timer = window.setTimeout(() => {
      let target: HTMLElement | null | undefined = null;
      if (focusTarget.to === 'notice') {
        target = noticeRef.current;
      } else if (focusTarget.to === 'failure') {
        target = failureRef.current;
      } else if (focusTarget.to === 'trigger') {
        target = trigger.current?.isConnected ? trigger.current : null;
      } else if (focusTarget.to === 'grade') {
        target = rowsRef.current?.querySelector<HTMLElement>(
          `[data-grade="${focusTarget.studentId}"]`,
        );
      }
      (target ?? headingRef.current)?.focus();
    }, 0);
    return () => window.clearTimeout(timer);
  }, [focusTarget]);

  function open(next: RosterDialog, from: HTMLElement) {
    trigger.current = from;
    setNotice(null);
    setFailure(null);
    setDialog(next);
  }

  function cancel() {
    setDialog(null);
    focusNext({ to: 'trigger' });
  }

  function reload() {
    setDialog(null);
    setFailure(null);
    actions?.onReload();
  }

  const rowOf = (studentId: string) =>
    assignment.roster.find((row) => row.student.id === studentId) ?? null;
  const gradeRow = dialog?.kind === 'grade' ? rowOf(dialog.studentId) : null;
  const recordRow = dialog?.kind === 'record' ? rowOf(dialog.studentId) : null;
  const hasActions =
    actions !== undefined && (assignment.can_edit || assignment.can_delete);

  return (
    <div className="flex w-full max-w-3xl min-w-0 flex-col gap-6">
      <div className="flex min-w-0 flex-col gap-3">
        <h1
          ref={headingRef}
          tabIndex={-1}
          className="text-2xl font-semibold break-words outline-none"
        >
          {assignment.title}
        </h1>
        <p className="flex min-w-0 flex-wrap gap-x-3 gap-y-1 text-sm">
          <span className="break-words">{assignment.subject.name}</span>
          <span>{dueText(assignment)}</span>
          <span>
            {assignment.max_score !== null
              ? t('assignments.maxScore', { max: assignment.max_score })
              : t('assignments.feedbackOnly')}
          </span>
        </p>
        <p className="flex min-w-0 flex-wrap gap-x-3 gap-y-1 text-sm text-muted-foreground">
          {assignment.class_session ? (
            <span>
              {t('assignments.forClassOn', {
                date: formatInstant(assignment.class_session.starts_at, 'date'),
              })}
            </span>
          ) : null}
          <span className="break-words">
            {t('assignments.setBy', { name: assignment.creator.display_name })}
          </span>
        </p>
        <p className={MULTILINE}>{assignment.instructions}</p>
        <p className="text-sm break-words">{countsText(assignment.counts)}</p>
      </div>

      {notice ? (
        <p
          ref={noticeRef}
          role="status"
          tabIndex={-1}
          className="text-sm font-medium outline-none"
        >
          {notice}
        </p>
      ) : null}
      {failure !== null ? (
        <AssignmentFailure
          ref={failureRef}
          error={failure}
          onReload={
            classifyRosterRefusal(failure) === 'other' ? undefined : reload
          }
        />
      ) : null}

      {hasActions ? (
        <div className="flex flex-wrap gap-2">
          {assignment.can_edit ? (
            <Link
              to={editAssignmentPath(assignment.id)}
              className={LINK_BUTTON_CLASS}
            >
              {t('assignments.actions.edit')}
            </Link>
          ) : null}
          {assignment.can_delete ? (
            <Button
              type="button"
              variant="outline"
              onClick={(event) => open({ kind: 'delete' }, event.currentTarget)}
            >
              {t('assignments.actions.delete')}
            </Button>
          ) : null}
        </div>
      ) : null}

      <section
        aria-labelledby={`${id}-students`}
        className="flex min-w-0 flex-col gap-3"
      >
        <h2 id={`${id}-students`} className="text-lg font-semibold">
          {t('assignments.roster.heading')}
        </h2>
        <ul ref={rowsRef} className="flex min-w-0 flex-col gap-3">
          {assignment.roster.map((row) => {
            // Strictly by the row's flags (C4).
            const canRecord = row.can_record_submission && !row.submitted_at;
            const canGrade =
              row.can_grade &&
              (row.state === 'submitted' || row.state === 'graded');
            return (
              <li
                key={row.student.id}
                className="flex min-w-0 flex-col gap-2 rounded-md border border-border p-4"
              >
                <div className="flex min-w-0 flex-wrap items-center gap-2">
                  <h3 className="min-w-0 text-base font-semibold break-words">
                    {row.student.display_name}
                  </h3>
                  <StateBadge state={row.state} />
                  <LateMarker late={row.is_late} />
                </div>
                {row.submitted_at ? (
                  <div className="flex min-w-0 flex-col gap-1 text-sm">
                    <p>
                      {t('assignments.handedInOn', {
                        when: formatInstant(row.submitted_at),
                      })}
                      {row.submitted_via === 'tutor'
                        ? ` · ${t('assignments.recordedByTutor')}`
                        : ''}
                    </p>
                    {row.text ? <p className={MULTILINE}>{row.text}</p> : null}
                    {row.url ? (
                      <p className="min-w-0">
                        <SafeLink url={row.url} />
                      </p>
                    ) : null}
                  </div>
                ) : null}
                {row.state === 'graded' ? (
                  <div className="flex min-w-0 flex-col gap-1 border-t border-border pt-2 text-sm">
                    {row.score !== null && assignment.max_score !== null ? (
                      <p className="font-medium">
                        {t('assignments.scoreWithPercent', {
                          score: row.score,
                          max: assignment.max_score,
                          percent: row.score_percent,
                        })}
                      </p>
                    ) : null}
                    {row.feedback ? (
                      <p className={MULTILINE}>{row.feedback}</p>
                    ) : null}
                    <p className="flex flex-wrap items-center gap-2 text-muted-foreground">
                      {row.graded_at ? (
                        <span>
                          {t('assignments.gradedOn', {
                            date: formatInstant(row.graded_at, 'date'),
                          })}
                        </span>
                      ) : null}
                      {row.grade_amended_at ? (
                        <span className={BADGE_CLASS}>
                          {t('assignments.updated')}
                        </span>
                      ) : null}
                    </p>
                    {row.last_change_reason ? (
                      <p className={MULTILINE}>
                        {t('assignments.roster.reason', {
                          reason: row.last_change_reason,
                        })}
                      </p>
                    ) : null}
                  </div>
                ) : null}
                {actions && (canRecord || canGrade) ? (
                  <div className="flex flex-wrap gap-2">
                    {canRecord ? (
                      <Button
                        type="button"
                        variant="outline"
                        size="sm"
                        aria-label={t('assignments.actions.recordFor', {
                          name: row.student.display_name,
                        })}
                        onClick={(event) =>
                          open(
                            { kind: 'record', studentId: row.student.id },
                            event.currentTarget,
                          )
                        }
                      >
                        {t('assignments.actions.record')}
                      </Button>
                    ) : null}
                    {canGrade ? (
                      <Button
                        type="button"
                        size="sm"
                        data-grade={row.student.id}
                        aria-label={
                          row.state === 'graded'
                            ? t('assignments.actions.regradeFor', {
                                name: row.student.display_name,
                              })
                            : t('assignments.actions.gradeFor', {
                                name: row.student.display_name,
                              })
                        }
                        onClick={(event) =>
                          open(
                            { kind: 'grade', studentId: row.student.id },
                            event.currentTarget,
                          )
                        }
                      >
                        {row.state === 'graded'
                          ? t('assignments.actions.regrade')
                          : t('assignments.actions.grade')}
                      </Button>
                    ) : null}
                  </div>
                ) : null}
              </li>
            );
          })}
        </ul>
      </section>

      {actions ? (
        <>
          <GradeDialog
            open={dialog?.kind === 'grade'}
            row={gradeRow}
            maxScore={assignment.max_score}
            onCancel={cancel}
            onReload={reload}
            onSave={async (body) => {
              const row = gradeRow!;
              await actions.onGrade(row, body);
              setDialog(null);
              setNotice(t('assignments.notices.graded'));
              focusNext({ to: 'grade', studentId: row.student.id });
            }}
          />
          <RecordSubmissionDialog
            open={dialog?.kind === 'record'}
            row={recordRow}
            onCancel={cancel}
            onReload={reload}
            onSave={async (body) => {
              await actions.onRecord(recordRow!, body);
              setDialog(null);
              setNotice(t('assignments.notices.recorded'));
              focusNext({ to: 'notice' });
            }}
          />
          <ConfirmDialog
            open={dialog?.kind === 'delete'}
            title={t('assignments.delete.title')}
            description={t('assignments.delete.description')}
            confirmLabel={t('assignments.delete.confirm')}
            busyLabel={t('assignments.busy')}
            cancelLabel={t('assignments.cancel')}
            destructive
            onCancel={cancel}
            onConfirm={async () => {
              try {
                await actions.onDelete();
              } catch (caught) {
                setDialog(null);
                setFailure(caught);
                focusNext({ to: 'failure' });
              }
            }}
          />
        </>
      ) : null}
    </div>
  );
}
