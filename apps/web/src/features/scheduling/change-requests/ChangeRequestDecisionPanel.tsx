import { useId, useRef, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { ErrorAlert } from '@/components/feedback/ErrorAlert';
import { Spinner } from '@/components/feedback/Spinner';
import { Button } from '@/components/ui/button';
import { SELECT_CLASS } from '@/features/students/styles';
import {
  conflictsOf,
  type Option,
  type SchedulingConflict,
} from '../create/api';
import { ConflictPanel } from '../create/ConflictPanel';
import { OverrideDialog } from '../create/OverrideDialog';
import { overrideAction, sessionOverride } from '../create/rules';
import type { ChangeRequestApproved, ChangeRequestReview } from './api';
import {
  DecisionReasonDialog,
  type DecisionReasonMode,
} from './DecisionReasonDialog';
import {
  isStaleChangeRequestError,
  KEEP_ROOM,
  NO_ROOM,
  type ApproveChoice,
} from './rules';

// The decision of a pending change request (#62 G1): "Approve" (for a
// reschedule with an optional room: keep the session's, another one or no
// room), "Approve as exception" (a dialog with a required reason: treated
// as excused) and "Reject" (a dialog with a required reason). A `409
// scheduling_conflict` of a reschedule shows the #48 panel and, for a
// holder of schedule.override with only soft conflicts, the #49 override
// dialog, which resends the same approval with the override. The page
// passes the calls; a refused decision because the request changed is
// handled there (a page-level message and a reload).

export interface ChangeRequestDecisionPanelProps {
  review: ChangeRequestReview;
  /** The active classrooms (a reschedule's room choice). */
  classrooms?: readonly Option[];
  canOverride: boolean;
  /** Approval is possible (pending, confirmed, not started). */
  canApprove: boolean;
  onApprove: (choice: ApproveChoice) => Promise<ChangeRequestApproved>;
  onApproved: (result: ChangeRequestApproved) => void;
  onReject: (reason: string) => Promise<void>;
  /** Stories. */
  initialConflicts?: SchedulingConflict[];
  initialDialog?: DecisionReasonMode;
  initialProblem?: 'required' | 'tooLong';
}

export function ChangeRequestDecisionPanel({
  review,
  classrooms,
  canOverride,
  canApprove,
  onApprove,
  onApproved,
  onReject,
  initialConflicts,
  initialDialog,
  initialProblem,
}: ChangeRequestDecisionPanelProps) {
  const { t } = useTranslation();
  const id = useId();
  const roomId = `${id}-room`;
  const reschedule = review.kind === 'reschedule';
  const [room, setRoom] = useState(KEEP_ROOM);
  const [conflicts, setConflicts] = useState<SchedulingConflict[] | null>(
    initialConflicts ?? null,
  );
  const [lastChoice, setLastChoice] = useState<ApproveChoice>({
    exception: false,
  });
  const [failure, setFailure] = useState<unknown>(null);
  const [busy, setBusy] = useState(false);
  const [dialog, setDialog] = useState<DecisionReasonMode | null>(
    initialDialog ?? null,
  );
  const [overriding, setOverriding] = useState(false);
  const inFlight = useRef(false);
  const panel = useRef<HTMLDivElement>(null);
  const overrideButton = useRef<HTMLButtonElement>(null);
  const focusPanel = useRef(false);

  const panelHeading = () =>
    panel.current?.querySelector<HTMLElement>('h2') ?? null;

  /** Approves; a conflict list is kept for the panel and resolves false. */
  async function attempt(choice: ApproveChoice): Promise<boolean> {
    setConflicts(null);
    setFailure(null);
    try {
      onApproved(await onApprove(choice));
      return true;
    } catch (caught) {
      const list = conflictsOf(caught);
      if (list) {
        setLastChoice(choice);
        setConflicts(list);
        return false;
      }
      throw caught;
    }
  }

  async function approve() {
    if (inFlight.current) {
      return;
    }
    inFlight.current = true;
    setBusy(true);
    try {
      await attempt({ exception: false, room });
    } catch (caught) {
      // A stale request: the page shows its message and reloads it.
      if (!isStaleChangeRequestError(caught)) {
        setFailure(caught);
      }
    } finally {
      inFlight.current = false;
      setBusy(false);
    }
  }

  async function approveAsException(reason: string) {
    try {
      const done = await attempt({ exception: true, reason, room });
      if (!done) {
        focusPanel.current = true;
      }
      setDialog(null);
    } catch (caught) {
      if (isStaleChangeRequestError(caught)) {
        setDialog(null);
        return;
      }
      throw caught;
    }
  }

  async function confirmOverride(reason: string) {
    if (!conflicts) {
      return;
    }
    try {
      await onApprove({
        ...lastChoice,
        override: sessionOverride(conflicts, reason),
      }).then(onApproved);
      setOverriding(false);
      setConflicts(null);
    } catch (caught) {
      const list = conflictsOf(caught);
      if (list) {
        setOverriding(false);
        setConflicts(list);
        return;
      }
      if (isStaleChangeRequestError(caught)) {
        setOverriding(false);
        return;
      }
      throw caught;
    }
  }

  const action = conflicts ? overrideAction(conflicts, canOverride) : null;

  return (
    <section
      aria-labelledby={`${id}-heading`}
      className="flex w-full max-w-2xl min-w-0 flex-col gap-4 rounded-md border border-border p-4"
    >
      <h2 id={`${id}-heading`} className="text-lg font-semibold">
        {t('scheduling.changeRequest.decide.heading')}
      </h2>
      <p className="text-sm text-muted-foreground">
        {reschedule
          ? t('scheduling.changeRequest.decide.introReschedule')
          : t('scheduling.changeRequest.decide.introCancellation')}
      </p>
      {conflicts ? (
        <div ref={panel} className="flex flex-col gap-3">
          <ConflictPanel
            conflicts={conflicts}
            heading={t('scheduling.changeRequest.decide.conflictsHeading', {
              count: conflicts.length,
            })}
            hint={t('scheduling.changeRequest.decide.conflictsHint')}
          />
          {action === 'override' ? (
            <div>
              <Button
                ref={overrideButton}
                type="button"
                onClick={() => setOverriding(true)}
              >
                {t('scheduling.changeRequest.decide.overrideAction')}
              </Button>
            </div>
          ) : (
            <p className="text-sm">
              {action === 'fixHardFirst'
                ? t('scheduling.changeRequest.decide.fixHardFirst')
                : t('scheduling.override.notAllowed')}
            </p>
          )}
          <OverrideDialog
            open={overriding && action === 'override'}
            conflicts={conflicts.filter((c) => c.severity === 'soft')}
            onConfirm={confirmOverride}
            onCancel={() => setOverriding(false)}
            onCloseAutoFocus={(event) => {
              event.preventDefault();
              (overrideButton.current ?? panelHeading())?.focus();
            }}
            labels={{
              title: t('scheduling.changeRequest.decide.overrideAction'),
              description: t(
                'scheduling.changeRequest.decide.overrideDescription',
              ),
              submit: t('scheduling.changeRequest.decide.overrideAction'),
              submitting: t('scheduling.changeRequest.decide.approving'),
            }}
          />
        </div>
      ) : null}
      {failure ? <ErrorAlert error={failure} /> : null}
      {canApprove && reschedule && classrooms ? (
        <div className="flex flex-col gap-2">
          <label htmlFor={roomId} className="text-sm font-medium">
            {t('scheduling.changeRequest.decide.room')}
          </label>
          <select
            id={roomId}
            className={SELECT_CLASS}
            value={room}
            disabled={busy}
            onChange={(event) => setRoom(event.target.value)}
          >
            <option value={KEEP_ROOM}>
              {review.session.classroom
                ? t('scheduling.changeRequest.decide.keepRoom', {
                    room: review.session.classroom.name,
                  })
                : t('scheduling.changeRequest.decide.keepNoRoom')}
            </option>
            {review.session.classroom ? (
              <option value={NO_ROOM}>{t('scheduling.noClassroom')}</option>
            ) : null}
            {classrooms
              .filter((option) => option.id !== review.session.classroom?.id)
              .map((option) => (
                <option key={option.id} value={option.id}>
                  {option.name}
                </option>
              ))}
          </select>
        </div>
      ) : null}
      {canApprove ? null : (
        <p className="text-sm">
          {t('scheduling.changeRequest.decide.cannotApprove')}
        </p>
      )}
      <div className="flex flex-col gap-2 sm:flex-row sm:flex-wrap">
        {canApprove ? (
          <>
            <Button
              type="button"
              disabled={busy}
              onClick={() => void approve()}
            >
              {busy ? (
                <Spinner
                  label={t('scheduling.changeRequest.decide.approving')}
                  showLabel
                />
              ) : reschedule ? (
                t('scheduling.changeRequest.decide.approveReschedule')
              ) : (
                t('scheduling.changeRequest.decide.approveCancellation')
              )}
            </Button>
            {review.request_outcome.classification === 'excused' ? null : (
              <Button
                type="button"
                variant="outline"
                disabled={busy}
                onClick={() => setDialog('exception')}
              >
                {t('scheduling.changeRequest.decide.exception')}
              </Button>
            )}
          </>
        ) : null}
        <Button
          type="button"
          variant="outline"
          disabled={busy}
          onClick={() => setDialog('reject')}
        >
          {t('scheduling.changeRequest.decide.reject')}
        </Button>
      </div>
      <DecisionReasonDialog
        open={dialog !== null}
        mode={dialog ?? 'reject'}
        initialProblem={initialProblem}
        onCancel={() => setDialog(null)}
        closeFocusTarget={() => {
          if (!focusPanel.current) {
            return null;
          }
          focusPanel.current = false;
          return panelHeading();
        }}
        onConfirm={async (reason) => {
          if (dialog === 'exception') {
            await approveAsException(reason);
          } else {
            await onReject(reason);
            setDialog(null);
          }
        }}
      />
    </section>
  );
}
