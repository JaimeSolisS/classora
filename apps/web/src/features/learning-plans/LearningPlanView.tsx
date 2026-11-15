import { CircleCheck, TriangleAlert } from 'lucide-react';
import { useEffect, useId, useRef, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { Link } from 'react-router';
import { Spinner } from '@/components/feedback/Spinner';
import { Button } from '@/components/ui/button';
import { ConfirmDialog } from '@/features/academics/evaluations/ConfirmDialog';
import { LINK_BUTTON_CLASS, SELECT_CLASS } from '@/features/students/styles';
import { useFormatters } from '@/lib/formatters';
import {
  editPlanPath,
  MILESTONE_STATUSES,
  type LearningPlan,
  type LearningPlanMilestone,
  type MilestoneStatus,
  type PlanAction,
} from './api';
import { PlanFailure, type PlanFailureState } from './PlanFailure';
import { OverdueMarker, PlanDates } from './PlanList';
import { ProgressMeter } from './ProgressMeter';
import { classifyRefusal, notDoneCount } from './rules';
import { StatusBadge } from './StatusBadge';

// One learning plan (#310 C1-C6, E3), shared by the staff and tutor page
// and the family page: the title (h1), status, subject, dates, goal,
// creator, activation and closing dates, the plan's progress, then each
// objective (h2 "{position}. {title}", its progress, "Complete") with its
// milestones (title, activities, due date, "Overdue", the status as text,
// "Done on {date} by {name}"). Every text is plain text.
//
// Staff audience only, strictly by the API's can_* flags (no flag, no
// control): "Edit", Activate / Complete / Discontinue / Delete draft (each
// confirmed in a dialog, one request), and per milestone a "Status of …"
// select (can_update_progress) that saves at once with the milestone's
// version and goes back to the previous value when refused. The family
// audience never renders a control, whatever the flags say.
//
// Focus: Cancel or Escape in a dialog returns it to the button that opened
// it; a done transition moves it to the status message; a refused write to
// its failure region; a saved progress back to its select (the live region
// says "Progress saved."), a refused one to its message.

export type PlanDialog = 'activate' | 'complete' | 'discontinue' | 'delete';

export interface PlanActions {
  /** A transition; resolves with the stored plan. */
  onTransition: (action: PlanAction) => Promise<LearningPlan>;
  /** Deletes the draft; the page navigates away. */
  onDelete: () => Promise<void>;
  /** One milestone's progress; resolves with the stored plan. */
  onProgress: (
    milestone: LearningPlanMilestone,
    status: MilestoneStatus,
  ) => Promise<LearningPlan>;
  /** Refetches after a conflict or a changed state (the page remounts the
   * view and focuses its heading). */
  onReload: () => void;
}

export interface LearningPlanViewProps {
  plan: LearningPlan;
  audience: 'staff' | 'family';
  /** The student's name for the dialogs (null: "The student…"). */
  studentName?: string | null;
  actions?: PlanActions;
  /** A notice to show first, focused ("Draft saved." after the create). */
  initialNotice?: string | null;
  /** Focus the heading when it mounts (after a Reload). */
  focusHeadingOnMount?: boolean;
  /** Stories: open a dialog. */
  initialDialog?: PlanDialog | null;
  /** Stories: a failure to show. */
  initialFailure?: PlanFailureState | null;
}

type FocusTarget =
  | { to: 'notice' | 'failure' | 'trigger' | 'heading' }
  | { to: 'select' | 'milestoneFailure'; milestoneId: string };

const MULTILINE = 'min-w-0 text-sm break-words whitespace-pre-line';

const TRANSITION_NOTICES: Record<PlanAction, string> = {
  activate: 'learningPlans.notices.activated',
  complete: 'learningPlans.notices.completed',
  discontinue: 'learningPlans.notices.discontinued',
};

export function LearningPlanView({
  plan,
  audience,
  studentName = null,
  actions,
  initialNotice = null,
  focusHeadingOnMount = false,
  initialDialog = null,
  initialFailure = null,
}: LearningPlanViewProps) {
  const { t } = useTranslation();
  const tAny = t as unknown as (key: string) => string;
  const { formatInstant } = useFormatters();
  const id = useId();
  const staff = audience === 'staff' && actions !== undefined;
  const [notice, setNotice] = useState<string | null>(initialNotice);
  const [failure, setFailure] = useState<PlanFailureState | null>(
    initialFailure,
  );
  const [dialog, setDialog] = useState<PlanDialog | null>(
    staff ? initialDialog : null,
  );
  const [saving, setSaving] = useState<{
    milestoneId: string;
    status: MilestoneStatus;
  } | null>(null);
  const [milestoneFailure, setMilestoneFailure] = useState<{
    milestoneId: string;
    failure: PlanFailureState;
  } | null>(null);
  const [announcement, setAnnouncement] = useState('');
  const trigger = useRef<HTMLElement | null>(null);
  const headingRef = useRef<HTMLHeadingElement>(null);
  const noticeRef = useRef<HTMLParagraphElement>(null);
  const failureRef = useRef<HTMLDivElement>(null);
  const objectivesRef = useRef<HTMLDivElement>(null);
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
      } else if (focusTarget.to === 'select') {
        target = objectivesRef.current?.querySelector<HTMLElement>(
          `[data-milestone-select="${focusTarget.milestoneId}"]`,
        );
      } else if (focusTarget.to === 'milestoneFailure') {
        target = objectivesRef.current?.querySelector<HTMLElement>(
          `[data-milestone-failure="${focusTarget.milestoneId}"]`,
        );
      }
      (target ?? headingRef.current)?.focus();
    }, 0);
    return () => window.clearTimeout(timer);
  }, [focusTarget]);

  function reload() {
    setFailure(null);
    setMilestoneFailure(null);
    actions?.onReload();
  }

  function openDialog(which: PlanDialog, from: HTMLElement) {
    trigger.current = from;
    setNotice(null);
    setDialog(which);
  }

  function cancelDialog() {
    setDialog(null);
    focusNext({ to: 'trigger' });
  }

  function refused(caught: unknown) {
    setDialog(null);
    setNotice(null);
    setFailure({ refusal: classifyRefusal(caught), error: caught });
    focusNext({ to: 'failure' });
  }

  async function transition(action: PlanAction) {
    if (!actions) {
      return;
    }
    try {
      await actions.onTransition(action);
      setDialog(null);
      setFailure(null);
      setNotice(tAny(TRANSITION_NOTICES[action]));
      focusNext({ to: 'notice' });
    } catch (caught) {
      refused(caught);
    }
  }

  async function progress(
    milestone: LearningPlanMilestone,
    status: MilestoneStatus,
  ) {
    if (!actions || saving) {
      return;
    }
    setSaving({ milestoneId: milestone.id, status });
    setMilestoneFailure(null);
    setAnnouncement('');
    try {
      await actions.onProgress(milestone, status);
      setSaving(null);
      setAnnouncement(t('learningPlans.progressSaved'));
      focusNext({ to: 'select', milestoneId: milestone.id });
    } catch (caught) {
      setSaving(null);
      setMilestoneFailure({
        milestoneId: milestone.id,
        failure: {
          refusal: classifyRefusal(caught, { milestone: true }),
          error: caught,
        },
      });
      focusNext({ to: 'milestoneFailure', milestoneId: milestone.id });
    }
  }

  const name = studentName ?? null;
  const objectives = [...plan.objectives].sort(
    (a, b) => a.position - b.position,
  );
  const hasActions =
    staff &&
    (plan.can_edit ||
      plan.can_activate ||
      plan.can_complete ||
      plan.can_discontinue ||
      plan.can_delete);
  const notDone = notDoneCount(plan);

  return (
    <div className="flex w-full max-w-3xl min-w-0 flex-col gap-6">
      <div className="flex min-w-0 flex-col gap-3">
        <h1
          ref={headingRef}
          tabIndex={-1}
          className="text-2xl font-semibold break-words outline-none"
        >
          {plan.title}
        </h1>
        <div className="flex min-w-0 flex-wrap items-center gap-x-3 gap-y-2">
          <StatusBadge status={plan.status} />
          <span className="text-sm break-words">{plan.subject.name}</span>
        </div>
        <p className="flex min-w-0 flex-wrap gap-x-3 gap-y-1 text-sm text-muted-foreground">
          <PlanDates startsOn={plan.starts_on} targetOn={plan.target_on} />
          <span className="break-words">
            {t('learningPlans.createdBy', {
              name: plan.created_by.display_name,
            })}
          </span>
        </p>
        {plan.activated_at || plan.closed_at ? (
          <p className="flex min-w-0 flex-wrap gap-x-3 gap-y-1 text-sm text-muted-foreground">
            {plan.activated_at ? (
              <span>
                {t('learningPlans.activatedOn', {
                  date: formatInstant(plan.activated_at, 'date'),
                })}
              </span>
            ) : null}
            {plan.closed_at && plan.status === 'completed' ? (
              <span>
                {t('learningPlans.completedOn', {
                  date: formatInstant(plan.closed_at, 'date'),
                })}
              </span>
            ) : null}
            {plan.closed_at && plan.status === 'discontinued' ? (
              <span>
                {t('learningPlans.discontinuedOn', {
                  date: formatInstant(plan.closed_at, 'date'),
                })}
              </span>
            ) : null}
          </p>
        ) : null}
        {plan.description ? (
          <p className={MULTILINE}>{plan.description}</p>
        ) : null}
        <ProgressMeter progress={plan.progress} />
        <p className="flex min-w-0 flex-wrap gap-x-3 gap-y-1 text-sm">
          <span>
            {t('learningPlans.objectivesComplete', {
              done: plan.objectives_complete,
              count: plan.objectives_total,
            })}
          </span>
          <OverdueMarker count={plan.overdue_milestones} />
        </p>
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
      {failure ? (
        <PlanFailure
          ref={failureRef}
          failure={failure}
          subjectName={plan.subject.name}
          studentId={plan.student_id}
          onReload={reload}
        />
      ) : null}

      {hasActions ? (
        <div className="flex flex-wrap gap-2">
          {plan.can_edit ? (
            <Link to={editPlanPath(plan.id)} className={LINK_BUTTON_CLASS}>
              {t('learningPlans.actions.edit')}
            </Link>
          ) : null}
          {plan.can_activate ? (
            <Button
              type="button"
              onClick={(event) => openDialog('activate', event.currentTarget)}
            >
              {t('learningPlans.actions.activate')}
            </Button>
          ) : null}
          {plan.can_complete ? (
            <Button
              type="button"
              onClick={(event) => openDialog('complete', event.currentTarget)}
            >
              {t('learningPlans.actions.complete')}
            </Button>
          ) : null}
          {plan.can_discontinue ? (
            <Button
              type="button"
              variant="outline"
              onClick={(event) =>
                openDialog('discontinue', event.currentTarget)
              }
            >
              {t('learningPlans.actions.discontinue')}
            </Button>
          ) : null}
          {plan.can_delete ? (
            <Button
              type="button"
              variant="outline"
              onClick={(event) => openDialog('delete', event.currentTarget)}
            >
              {t('learningPlans.actions.delete')}
            </Button>
          ) : null}
        </div>
      ) : null}

      <p role="status" aria-live="polite" className="sr-only">
        {announcement}
      </p>

      <div ref={objectivesRef} className="flex min-w-0 flex-col gap-6">
        {objectives.map((objective) => (
          <section
            key={objective.id}
            aria-labelledby={`${id}-o-${objective.id}`}
            className="flex min-w-0 flex-col gap-3 rounded-md border border-border p-4"
          >
            <h2
              id={`${id}-o-${objective.id}`}
              className="text-lg font-semibold break-words"
            >
              {t('learningPlans.objectiveHeading', {
                position: objective.position,
                title: objective.title,
              })}
            </h2>
            {objective.complete ? (
              <p className="inline-flex items-center gap-1 text-sm font-medium">
                <CircleCheck aria-hidden="true" className="size-4 shrink-0" />
                {t('learningPlans.objectiveComplete')}
              </p>
            ) : null}
            {objective.description ? (
              <p className={MULTILINE}>{objective.description}</p>
            ) : null}
            <ProgressMeter progress={objective.progress} />
            <ol className="flex min-w-0 flex-col gap-3">
              {[...objective.milestones]
                .sort((a, b) => a.position - b.position)
                .map((milestone) => {
                  const selectId = `${id}-m-${milestone.id}`;
                  const mine =
                    milestoneFailure?.milestoneId === milestone.id
                      ? milestoneFailure.failure
                      : null;
                  const busy = saving?.milestoneId === milestone.id;
                  return (
                    <li
                      key={milestone.id}
                      className="flex min-w-0 flex-col gap-2 border-t border-border pt-3"
                    >
                      <h3 className="text-base font-semibold break-words">
                        {milestone.title}
                      </h3>
                      {milestone.activities ? (
                        <p className={MULTILINE}>{milestone.activities}</p>
                      ) : null}
                      <p className="flex min-w-0 flex-wrap items-center gap-x-3 gap-y-1 text-sm">
                        {milestone.due_on ? (
                          <DueDate dueOn={milestone.due_on} />
                        ) : null}
                        {milestone.overdue ? (
                          <span className="inline-flex items-center gap-1 font-medium text-destructive">
                            <TriangleAlert
                              aria-hidden="true"
                              className="size-4 shrink-0"
                            />
                            {t('learningPlans.overdue')}
                          </span>
                        ) : null}
                        {!(staff && plan.can_update_progress) ? (
                          <span className="font-medium">
                            {t(
                              `learningPlans.milestoneStatus.${milestone.status}`,
                            )}
                          </span>
                        ) : null}
                      </p>
                      {milestone.status === 'done' && milestone.completed_at ? (
                        <p className="text-sm break-words text-muted-foreground">
                          {milestone.completed_by
                            ? t('learningPlans.doneBy', {
                                date: formatInstant(
                                  milestone.completed_at,
                                  'date',
                                ),
                                name: milestone.completed_by.display_name,
                              })
                            : t('learningPlans.doneOn', {
                                date: formatInstant(
                                  milestone.completed_at,
                                  'date',
                                ),
                              })}
                        </p>
                      ) : null}
                      {staff && plan.can_update_progress ? (
                        <div className="flex min-w-0 flex-col gap-1.5">
                          <label
                            htmlFor={selectId}
                            className="text-sm font-medium break-words"
                          >
                            {t('learningPlans.statusOf', {
                              title: milestone.title,
                            })}
                          </label>
                          <div className="flex min-w-0 items-center gap-2">
                            <select
                              id={selectId}
                              data-milestone-select={milestone.id}
                              className={`${SELECT_CLASS} max-w-56`}
                              value={busy ? saving.status : milestone.status}
                              disabled={busy}
                              aria-describedby={
                                mine ? `${selectId}-error` : undefined
                              }
                              onChange={(event) =>
                                void progress(
                                  milestone,
                                  event.target.value as MilestoneStatus,
                                )
                              }
                            >
                              {MILESTONE_STATUSES.map((status) => (
                                <option key={status} value={status}>
                                  {t(`learningPlans.milestoneStatus.${status}`)}
                                </option>
                              ))}
                            </select>
                            {busy ? (
                              <Spinner
                                label={t('learningPlans.savingProgress')}
                              />
                            ) : null}
                          </div>
                          {mine ? (
                            <div
                              id={`${selectId}-error`}
                              data-milestone-failure={milestone.id}
                              tabIndex={-1}
                              className="min-w-0 outline-none"
                            >
                              <PlanFailure
                                failure={mine}
                                subjectName={plan.subject.name}
                                studentId={plan.student_id}
                                onReload={reload}
                              />
                            </div>
                          ) : null}
                        </div>
                      ) : null}
                    </li>
                  );
                })}
            </ol>
          </section>
        ))}
      </div>

      {staff ? (
        <>
          <ConfirmDialog
            open={dialog === 'activate'}
            title={t('learningPlans.activate.title')}
            description={
              name
                ? t('learningPlans.activate.description', { student: name })
                : t('learningPlans.activate.descriptionNoName')
            }
            confirmLabel={t('learningPlans.activate.confirm')}
            busyLabel={t('learningPlans.busy')}
            cancelLabel={t('learningPlans.cancel')}
            onCancel={cancelDialog}
            onConfirm={() => transition('activate')}
          />
          <ConfirmDialog
            open={dialog === 'complete'}
            title={t('learningPlans.complete.title')}
            description={
              name
                ? t('learningPlans.complete.description', { student: name })
                : t('learningPlans.complete.descriptionNoName')
            }
            confirmLabel={t('learningPlans.complete.confirm')}
            busyLabel={t('learningPlans.busy')}
            cancelLabel={t('learningPlans.cancel')}
            onCancel={cancelDialog}
            onConfirm={() => transition('complete')}
          >
            {notDone > 0 ? (
              <p className="text-sm">
                {t('learningPlans.complete.notDone', { count: notDone })}
              </p>
            ) : null}
          </ConfirmDialog>
          <ConfirmDialog
            open={dialog === 'discontinue'}
            title={t('learningPlans.discontinue.title')}
            description={t('learningPlans.discontinue.description')}
            confirmLabel={t('learningPlans.discontinue.confirm')}
            busyLabel={t('learningPlans.busy')}
            cancelLabel={t('learningPlans.cancel')}
            destructive
            onCancel={cancelDialog}
            onConfirm={() => transition('discontinue')}
          >
            <p className="text-sm">
              {plan.status === 'active'
                ? name
                  ? t('learningPlans.discontinue.active', { student: name })
                  : t('learningPlans.discontinue.activeNoName')
                : t('learningPlans.discontinue.draft')}
            </p>
          </ConfirmDialog>
          <ConfirmDialog
            open={dialog === 'delete'}
            title={t('learningPlans.delete.title')}
            description={t('learningPlans.delete.description')}
            confirmLabel={t('learningPlans.delete.confirm')}
            busyLabel={t('learningPlans.busy')}
            cancelLabel={t('learningPlans.cancel')}
            destructive
            onCancel={cancelDialog}
            onConfirm={async () => {
              try {
                await actions.onDelete();
              } catch (caught) {
                refused(caught);
              }
            }}
          />
        </>
      ) : null}
    </div>
  );
}

function DueDate({ dueOn }: { dueOn: string }) {
  const { t } = useTranslation();
  const { formatLocalDate } = useFormatters();
  return (
    <span>{t('learningPlans.due', { date: formatLocalDate(dueOn) })}</span>
  );
}
