import { useQuery, useQueryClient } from '@tanstack/react-query';
import { useEffect, useId, useRef, useState, type FormEvent } from 'react';
import { useTranslation } from 'react-i18next';
import { ErrorAlert } from '@/components/feedback/ErrorAlert';
import { Spinner } from '@/components/feedback/Spinner';
import { Button } from '@/components/ui/button';
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog';
import type { ErrorMessageKey } from '@/app/errors';
import { FieldError } from '@/features/auth/FieldError';
import { SlotList } from '@/features/family-portal/requests/RequestSessionView';
import type { SlotsState } from '@/features/family-portal/requests/RequestSessionView';
import type { AvailableSlot } from '@/features/family-portal/requests/api';
import { noteLength, slotTimes } from '@/features/family-portal/requests/rules';
import type { ScheduleEntry } from '@/features/my-schedule/api';
import { useReturnFocus } from '@/features/scheduling/series/hooks';
import { toLanguage } from '@/i18n/language';
import {
  addDays,
  formatInstant,
  formatLocalDate,
  todayInZone,
  type LocalDate,
} from '@/lib/time';
import {
  CHANGE_REQUEST_LOADERS,
  CURRENT_POLICY_KEY,
  RESCHEDULE_SLOTS_KEY,
  pendingRequestKey,
  type ChangeRequestDetail,
  type ChangeRequestKind,
  type ChangeRequestLoaders,
} from './api';
import { previewOutcome, previewTextKey } from './outcome';
import {
  canPageBack,
  canPageForward,
  createBody,
  durationMinutes,
  initiatorOf,
  NOTE_MAX_LENGTH,
  previousPage,
  proposalEnd,
  sendRefusal,
  SLOT_PAGE_DAYS,
  slotDuration,
  slotPage,
  tutorProposal,
  type ProposalProblem,
  type SendRefusal,
} from './rules';

// "Request a change" (#62b A2-A4): one dialog, two steps. Step 1 asks what
// is needed (cancel the class, or move it to another time). Step 2 has the
// details: for a family's reschedule the #59 slot picker (fed by
// GET /available-slots for the session's student, subject and length,
// 14 dates a page from today, up to 60 days ahead); for a tutor's, a date
// and a start time in the center zone with the end shown; the optional
// note (500 characters) and the outcome preview (an estimate, from the
// current scheduling policy; left out when it can't be loaded). Send posts
// the request; each refusal has its own message (role="alert"). Focus
// moves into the dialog, to each step's heading, and back to the opener.
// Escape closes it unless a request is being sent.

export interface RequestChangeDialogProps {
  open: boolean;
  entry: ScheduleEntry;
  /** The family student whose schedule this is (null for a tutor). */
  studentId: string | null;
  timeZone: string;
  /** When the dialog opened (epoch ms): the preview and the slot dates. */
  now: number;
  /** The clock of the "after now" check at send (tests and stories). */
  clock?: () => number;
  loaders?: ChangeRequestLoaders;
  onClose: () => void;
  onSent: (request: ChangeRequestDetail) => void;
  /** The session is no longer visible (404): refresh the schedule. */
  onScheduleStale?: () => void;
  /** Stories and tests: start on a step with a kind. */
  initialStep?: 1 | 2;
  initialKind?: ChangeRequestKind;
}

export function RequestChangeDialog(props: RequestChangeDialogProps) {
  const { t, i18n } = useTranslation();
  const language = toLanguage(i18n.resolvedLanguage);
  const busy = useRef(false);
  const returnFocus = useReturnFocus();
  return (
    <Dialog
      open={props.open}
      onOpenChange={(next) => {
        if (!next && !busy.current) {
          props.onClose();
        }
      }}
    >
      {props.open ? (
        <DialogContent
          {...returnFocus}
          onInteractOutside={(event) => event.preventDefault()}
          className="max-h-[calc(100dvh-2rem)] overflow-y-auto sm:max-w-lg"
        >
          <DialogHeader>
            <DialogTitle>{t('changeRequests.dialog.title')}</DialogTitle>
            <DialogDescription>
              {t('changeRequests.dialog.description', {
                subject: props.entry.item.subject.name,
                when: formatInstant(props.entry.item.starts_at, {
                  timeZone: props.timeZone,
                  language,
                  style: 'dateTime',
                }),
              })}
            </DialogDescription>
          </DialogHeader>
          <RequestChangeContent {...props} busyRef={busy} />
        </DialogContent>
      ) : null}
    </Dialog>
  );
}

function RequestChangeContent({
  entry,
  studentId,
  timeZone,
  now,
  loaders = CHANGE_REQUEST_LOADERS,
  onClose,
  onSent,
  onScheduleStale,
  clock = Date.now,
  initialStep = 1,
  initialKind = 'cancellation',
  busyRef,
}: RequestChangeDialogProps & { busyRef: { current: boolean } }) {
  const { t, i18n } = useTranslation();
  const { t: tErrors } = useTranslation('errors');
  const language = toLanguage(i18n.resolvedLanguage);
  const client = useQueryClient();
  const id = useId();
  const stepHeading = useRef<HTMLHeadingElement>(null);
  const noteField = useRef<HTMLTextAreaElement>(null);
  const timeField = useRef<HTMLInputElement>(null);
  const firstRender = useRef(true);

  const item = entry.item;
  const initiatedBy = initiatorOf(entry);
  const minutes = durationMinutes(item);
  const duration = slotDuration(minutes);
  const familyCanMove = initiatedBy === 'tutor' || duration !== null;
  const today = todayInZone(timeZone, new Date(now));

  const [step, setStep] = useState<1 | 2>(initialStep);
  const [kind, setKind] = useState<ChangeRequestKind>(
    initialKind === 'reschedule' && !familyCanMove
      ? 'cancellation'
      : initialKind,
  );
  const [note, setNote] = useState('');
  const [noteError, setNoteError] = useState<string | undefined>();
  const [pageStart, setPageStart] = useState<LocalDate>(today);
  const [slot, setSlot] = useState<string | null>(null);
  const [slotMissing, setSlotMissing] = useState(false);
  const [date, setDate] = useState('');
  const [time, setTime] = useState('');
  const [proposalError, setProposalError] = useState<
    | { kind: 'client'; problem: ProposalProblem }
    | { kind: 'server'; code: ErrorMessageKey }
    | null
  >(null);
  const [refusal, setRefusal] = useState<SendRefusal | null>(null);
  const [failure, setFailure] = useState<unknown>(null);
  const [sending, setSending] = useState(false);

  // Each step's heading takes the focus when the step changes.
  useEffect(() => {
    if (firstRender.current) {
      firstRender.current = false;
      return;
    }
    stepHeading.current?.focus();
  }, [step]);

  const policy = useQuery({
    queryKey: CURRENT_POLICY_KEY,
    queryFn: ({ signal }) => loaders.fetchCurrentPolicy(signal),
    retry: false,
    staleTime: 60_000,
  });

  const familyReschedule =
    step === 2 &&
    kind === 'reschedule' &&
    initiatedBy === 'family' &&
    duration !== null &&
    studentId !== null;
  const range = slotPage(pageStart, today);
  const slotsQuery = {
    studentId: studentId ?? '',
    subjectId: item.subject.id,
    duration: duration ?? 60,
    from: range.from,
    to: range.to,
  };
  const slots = useQuery({
    queryKey: [...RESCHEDULE_SLOTS_KEY, item.id, slotsQuery],
    queryFn: ({ signal }) => loaders.fetchSlots(slotsQuery, signal),
    enabled: familyReschedule,
    retry: false,
  });

  let slotsState: SlotsState = { status: 'idle' };
  if (familyReschedule) {
    if (slots.isError) {
      slotsState = {
        status: 'error',
        error: slots.error,
        onRetry: () => void slots.refetch(),
      };
    } else if (slots.data) {
      slotsState = { status: 'ready', data: slots.data };
    } else {
      slotsState = { status: 'loading' };
    }
  }

  const preview = policy.data
    ? previewOutcome(policy.data, {
        kind,
        initiatedBy,
        startsAt: item.starts_at,
        now,
      })
    : null;
  const previewKey = preview ? previewTextKey(kind, preview) : null;
  const length = noteLength(note);

  const tutorStart =
    initiatedBy === 'tutor' && date && time
      ? tutorProposal(date, time, timeZone, Number.NEGATIVE_INFINITY)
      : null;
  const tutorEnd =
    tutorStart && 'startsAt' in tutorStart
      ? formatInstant(proposalEnd(tutorStart.startsAt, minutes), {
          timeZone,
          language,
          style: 'time',
        })
      : null;

  function chooseSlot(chosen: AvailableSlot) {
    setSlot(chosen.starts_at);
    setSlotMissing(false);
  }

  function clearMessages() {
    setRefusal(null);
    setFailure(null);
  }

  async function send() {
    clearMessages();
    if (length > NOTE_MAX_LENGTH) {
      setNoteError(t('changeRequests.dialog.noteTooLong'));
      noteField.current?.focus();
      return;
    }
    setNoteError(undefined);
    let proposedStartsAt: string | undefined;
    if (kind === 'reschedule') {
      if (initiatedBy === 'family') {
        if (!slot) {
          setSlotMissing(true);
          return;
        }
        proposedStartsAt = slot;
      } else {
        const proposal = tutorProposal(date, time, timeZone, clock());
        if ('problem' in proposal) {
          setProposalError({ kind: 'client', problem: proposal.problem });
          timeField.current?.focus();
          return;
        }
        setProposalError(null);
        proposedStartsAt = proposal.startsAt;
      }
    }
    busyRef.current = true;
    setSending(true);
    try {
      const created = await loaders.createChangeRequest(
        createBody({ sessionId: item.id, kind, proposedStartsAt, note }),
      );
      onSent(created);
    } catch (caught) {
      const refused = sendRefusal(caught);
      if (refused?.kind === 'field') {
        setProposalError({ kind: 'server', code: refused.code });
        timeField.current?.focus();
      } else if (refused) {
        setRefusal(refused);
        if (refused.refetch === 'slots') {
          setSlot(null);
          void client.invalidateQueries({ queryKey: RESCHEDULE_SLOTS_KEY });
        } else if (refused.refetch === 'pending') {
          void client.invalidateQueries({
            queryKey: pendingRequestKey(item.id),
          });
        } else if (refused.refetch === 'schedule') {
          onScheduleStale?.();
        }
      } else {
        setFailure(caught);
      }
    } finally {
      busyRef.current = false;
      setSending(false);
    }
  }

  function onSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (busyRef.current) {
      return;
    }
    if (step === 1) {
      clearMessages();
      setStep(2);
      return;
    }
    void send();
  }

  function refusalText(refused: SendRefusal & { kind: 'message' }): string {
    switch (refused.code) {
      case 'student_consent_required':
        return tErrors('student_consent_required');
      case 'rate_limited':
        return t('changeRequests.errors.rate_limited', {
          count: refused.minutes ?? 1,
        });
      default:
        return t(`changeRequests.errors.${refused.code}`);
    }
  }

  let proposalMessage: string | undefined;
  if (proposalError?.kind === 'server') {
    proposalMessage = tErrors(proposalError.code);
  } else if (proposalError?.kind === 'client') {
    proposalMessage = t(
      `changeRequests.dialog.proposal.${proposalError.problem}`,
    );
  }
  const timeErrorId = `${id}-time-error`;
  const noteErrorId = `${id}-note-error`;
  const counterId = `${id}-note-counter`;

  return (
    <form
      noValidate
      onSubmit={onSubmit}
      className="flex min-w-0 flex-col gap-4"
    >
      <h3
        ref={stepHeading}
        tabIndex={-1}
        className="text-sm font-medium text-muted-foreground outline-none focus-visible:ring-3 focus-visible:ring-ring"
      >
        {step === 1
          ? t('changeRequests.dialog.step1')
          : t('changeRequests.dialog.step2')}
      </h3>

      {step === 1 ? (
        <fieldset className="flex min-w-0 flex-col gap-2">
          <legend className="mb-1 font-medium">
            {t('changeRequests.dialog.what')}
          </legend>
          <label className="flex min-h-6 items-center gap-2">
            <input
              type="radio"
              name={`${id}-kind`}
              value="cancellation"
              checked={kind === 'cancellation'}
              onChange={() => setKind('cancellation')}
              className="size-4 shrink-0"
            />
            {t('changeRequests.dialog.cancel')}
          </label>
          <label className="flex min-h-6 items-center gap-2">
            <input
              type="radio"
              name={`${id}-kind`}
              value="reschedule"
              checked={kind === 'reschedule'}
              disabled={!familyCanMove}
              aria-describedby={familyCanMove ? undefined : `${id}-cant-move`}
              onChange={() => setKind('reschedule')}
              className="size-4 shrink-0"
            />
            {t('changeRequests.dialog.move')}
          </label>
          {familyCanMove ? null : (
            <p id={`${id}-cant-move`} className="text-sm">
              {t('changeRequests.dialog.cantMove')}
            </p>
          )}
        </fieldset>
      ) : (
        <>
          <p className="text-sm">
            <span className="font-medium">
              {t('changeRequests.dialog.chosen')}:
            </span>{' '}
            {kind === 'cancellation'
              ? t('changeRequests.dialog.cancel')
              : t('changeRequests.dialog.move')}
          </p>

          {kind === 'reschedule' && initiatedBy === 'family' ? (
            <section
              aria-labelledby={`${id}-slots`}
              className="flex min-w-0 flex-col gap-3"
            >
              <h4 id={`${id}-slots`} className="font-medium">
                {t('changeRequests.dialog.slots', {
                  from: formatLocalDate(range.from, { language }),
                  to: formatLocalDate(range.to, { language }),
                })}
              </h4>
              <p className="text-sm text-muted-foreground">
                {t('changeRequests.dialog.slotsHelp', { zone: timeZone })}
              </p>
              <div className="flex flex-wrap gap-2">
                <Button
                  type="button"
                  variant="outline"
                  disabled={!canPageBack(pageStart, today)}
                  onClick={() => setPageStart(previousPage(pageStart, today))}
                >
                  {t('changeRequests.dialog.earlier')}
                </Button>
                <Button
                  type="button"
                  variant="outline"
                  disabled={!canPageForward(pageStart, today)}
                  onClick={() =>
                    setPageStart(addDays(pageStart, SLOT_PAGE_DAYS))
                  }
                >
                  {t('changeRequests.dialog.later')}
                </Button>
              </div>
              <SlotList
                slots={slotsState}
                ready
                timeZone={timeZone}
                onChoose={chooseSlot}
                selected={slot}
              />
              <p aria-live="polite" className="text-sm">
                {slot
                  ? t('changeRequests.dialog.newTime', {
                      when: t('sessionRequests.when', {
                        ...slotTimes(
                          {
                            starts_at: slot,
                            ends_at: proposalEnd(slot, minutes),
                          },
                          timeZone,
                          language,
                        ),
                      }),
                    })
                  : null}
              </p>
              {slotMissing ? (
                <p role="alert" className="text-sm text-destructive">
                  {t('changeRequests.dialog.chooseSlot')}
                </p>
              ) : null}
            </section>
          ) : null}

          {kind === 'reschedule' && initiatedBy === 'tutor' ? (
            <fieldset className="flex min-w-0 flex-col gap-3">
              <legend className="mb-1 font-medium">
                {t('changeRequests.dialog.newTimeLegend', { zone: timeZone })}
              </legend>
              <div className="flex min-w-0 flex-wrap gap-3">
                <div className="flex min-w-0 flex-col gap-1">
                  <label htmlFor={`${id}-date`} className="text-sm font-medium">
                    {t('changeRequests.dialog.date')}
                  </label>
                  <input
                    id={`${id}-date`}
                    type="date"
                    min={today}
                    value={date}
                    disabled={sending}
                    aria-invalid={proposalMessage ? true : undefined}
                    aria-describedby={proposalMessage ? timeErrorId : undefined}
                    onChange={(event) => {
                      setDate(event.target.value);
                      setProposalError(null);
                    }}
                    className="h-9 min-w-0 rounded-md border border-input bg-background px-2.5 text-base md:text-sm"
                  />
                </div>
                <div className="flex min-w-0 flex-col gap-1">
                  <label htmlFor={`${id}-time`} className="text-sm font-medium">
                    {t('changeRequests.dialog.time')}
                  </label>
                  <input
                    id={`${id}-time`}
                    ref={timeField}
                    type="time"
                    step={60}
                    value={time}
                    disabled={sending}
                    aria-invalid={proposalMessage ? true : undefined}
                    aria-describedby={proposalMessage ? timeErrorId : undefined}
                    onChange={(event) => {
                      setTime(event.target.value);
                      setProposalError(null);
                    }}
                    className="h-9 min-w-0 rounded-md border border-input bg-background px-2.5 text-base md:text-sm"
                  />
                </div>
              </div>
              <p className="text-sm" aria-live="polite">
                {tutorEnd
                  ? t('changeRequests.dialog.end', {
                      time: tutorEnd,
                      minutes,
                    })
                  : t('changeRequests.dialog.sameLength', { minutes })}
              </p>
              {proposalMessage ? (
                <p
                  id={timeErrorId}
                  role="alert"
                  className="text-sm text-destructive"
                >
                  {proposalMessage}
                </p>
              ) : null}
            </fieldset>
          ) : null}

          <div className="flex flex-col gap-2">
            <label htmlFor={`${id}-note`} className="text-sm font-medium">
              {t('changeRequests.dialog.note')}
            </label>
            <textarea
              id={`${id}-note`}
              ref={noteField}
              rows={3}
              value={note}
              disabled={sending}
              aria-invalid={noteError ? true : undefined}
              aria-describedby={[noteError ? noteErrorId : undefined, counterId]
                .filter(Boolean)
                .join(' ')}
              onChange={(event) => setNote(event.target.value)}
              className="w-full min-w-0 rounded-md border border-input bg-transparent px-2.5 py-1.5 text-base shadow-xs outline-none focus-visible:border-ring focus-visible:ring-3 focus-visible:ring-ring disabled:cursor-not-allowed disabled:opacity-50 aria-invalid:border-destructive md:text-sm dark:bg-input/30"
            />
            <FieldError id={noteErrorId} message={noteError} />
            <p
              id={counterId}
              aria-live="polite"
              className={
                length > NOTE_MAX_LENGTH
                  ? 'text-sm text-destructive'
                  : 'text-sm text-muted-foreground'
              }
            >
              {t('changeRequests.dialog.counter', {
                length,
                max: NOTE_MAX_LENGTH,
              })}
            </p>
          </div>

          {preview && previewKey ? (
            <section
              aria-labelledby={`${id}-preview`}
              className="flex flex-col gap-1 rounded-md border border-border p-3"
            >
              <h4 id={`${id}-preview`} className="text-sm font-medium">
                {t('changeRequests.preview.heading')}
              </h4>
              <p className="text-sm">
                {t(previewKey, {
                  hours: policy.data?.change_notice_hours ?? 0,
                  date: preview.makeupUntil
                    ? formatInstant(preview.makeupUntil, {
                        timeZone,
                        language,
                        style: 'date',
                      })
                    : '',
                })}
              </p>
              <p className="text-xs text-muted-foreground">
                {t('changeRequests.preview.estimate')}
              </p>
            </section>
          ) : null}
        </>
      )}

      {refusal?.kind === 'message' ? (
        <p
          role="alert"
          className="rounded-md border border-destructive p-3 text-sm"
        >
          {refusalText(refusal)}
        </p>
      ) : null}
      {failure ? <ErrorAlert error={failure} /> : null}

      <DialogFooter>
        {step === 2 ? (
          <Button
            type="button"
            variant="outline"
            disabled={sending}
            onClick={() => {
              clearMessages();
              setStep(1);
            }}
          >
            {t('changeRequests.dialog.back')}
          </Button>
        ) : (
          <Button type="button" variant="outline" onClick={onClose}>
            {t('changeRequests.dialog.close')}
          </Button>
        )}
        <Button type="submit" disabled={sending}>
          {step === 1 ? (
            t('changeRequests.dialog.next')
          ) : sending ? (
            <Spinner label={t('changeRequests.dialog.sending')} showLabel />
          ) : (
            t('changeRequests.dialog.send')
          )}
        </Button>
      </DialogFooter>
    </form>
  );
}
