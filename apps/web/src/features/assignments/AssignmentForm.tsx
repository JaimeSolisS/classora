import { zodResolver } from '@hookform/resolvers/zod';
import {
  useEffect,
  useId,
  useMemo,
  useRef,
  useState,
  type FormEvent,
} from 'react';
import {
  Controller,
  useForm,
  useWatch,
  type FieldErrors,
  type FieldPath,
} from 'react-hook-form';
import { useTranslation } from 'react-i18next';
import { useBlocker } from 'react-router';
import { ApiError } from '@/app/api';
import { errorMessage } from '@/app/errors';
import { Spinner } from '@/components/feedback/Spinner';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Counter } from '@/features/academics/Counter';
import { ConfirmDialog } from '@/features/academics/evaluations/ConfirmDialog';
import { LeaveDialog } from '@/features/academics/LeaveDialog';
import { TEXTAREA_CLASS } from '@/features/academics/styles';
import { FieldError } from '@/features/auth/FieldError';
import { SELECT_CLASS } from '@/features/students/styles';
import { useFormatters } from '@/lib/formatters';
import type { AssignmentView } from './api';
import { AssignmentFailure } from './AssignmentFailure';
import { RecipientPicker, type PickerStudent } from './RecipientPicker';
import {
  assignmentFormSchema,
  classifyFormRefusal,
  detailStudentIds,
  formValuesOf,
  INSTRUCTIONS_MAX,
  latestDueOn,
  lockedRecipients,
  newFormValues,
  placeServerErrors,
  type AssignmentFormValues,
  type FormField,
  type FormMessage,
} from './rules';

// The homework form (#313 B), create and edit, phone-first, React Hook
// Form + Zod: Subject (create: a select of the active subjects; from a
// class: its subject as text; edit: read-only text), Title, Instructions
// (counter), Due date (min the center's today, max today + 365), Due time
// (optional), Max score (optional, lib/decimal; disabled once graded) and
// the recipients (RecipientPicker). One request per save (the page sends it
// and navigates); a refusal lands on its fields (focus on the first; a
// refused student on its checkbox) or in the failure region (focused).
// Reload after a conflict asks first when something was typed, then the
// page refetches and remounts the form, which focuses its heading. Leaving
// with unsaved changes asks first (the #77 leave pattern). Fields are never
// disabled while a save runs (only the button), so they can take focus.

/** The class the homework is set for (B1). */
export interface SessionContext {
  id: string;
  startsAt: string;
  subject: { id: string; name: string; level: string };
  participants: readonly PickerStudent[];
}

export interface SubjectChoice {
  id: string;
  label: string;
}

/** What the failure region shows. */
export type FormFailure =
  { kind: 'session' } | { kind: 'error'; error: unknown; reload: boolean };

export interface AssignmentFormProps {
  /** The assignment being edited; null on a create. */
  assignment: AssignmentView | null;
  /** Create from a class (B1). */
  session?: SessionContext | null;
  /** Create without a class: the active subjects. */
  subjects?: readonly SubjectChoice[];
  /** Edit of a class's homework: its participants (else the search). */
  participants?: readonly PickerStudent[] | null;
  /** The center's today and wall-clock time (HH:MM). */
  today: string;
  nowTime: string;
  /** Saves (create or edit) and navigates; rejects with the error.
   * `version` is the version the form was loaded from (null on a create). */
  onSave: (
    values: AssignmentFormValues,
    original: AssignmentFormValues | null,
    version: number | null,
  ) => Promise<void>;
  /** Refetches and remounts (after a conflict or a refusal). */
  onReload: () => void;
  /** The form without its class (B1 link). */
  withoutSessionPath: string;
  /** Focus the heading when it mounts (after a Reload). */
  focusHeadingOnMount?: boolean;
}

type FocusTarget =
  | { to: 'heading' | 'failure' | 'trigger' }
  | { to: 'field'; name: FormField }
  | { to: 'student'; id: string };

const FIELD_ORDER: readonly FormField[] = [
  'subjectId',
  'title',
  'instructions',
  'dueOn',
  'dueTime',
  'maxScore',
  'studentIds',
];

function useMessage() {
  const { t } = useTranslation();
  const { t: tErrors } = useTranslation('errors');
  return (message: FormMessage | undefined): string | undefined => {
    if (!message) {
      return undefined;
    }
    if (message.startsWith('errors:')) {
      return errorMessage(
        new ApiError({ code: message.slice('errors:'.length), status: 422 }),
        tErrors,
      );
    }
    return (t as unknown as (key: string) => string)(message);
  };
}

export function AssignmentForm({
  assignment,
  session = null,
  subjects = [],
  participants = null,
  today,
  nowTime,
  onSave,
  onReload,
  withoutSessionPath,
  focusHeadingOnMount = false,
}: AssignmentFormProps) {
  const { t } = useTranslation();
  const { t: tErrors } = useTranslation('errors');
  const { formatInstant } = useFormatters();
  const message = useMessage();
  const id = useId();
  const editing = assignment !== null;
  const original = useMemo(
    () => (assignment ? formValuesOf(assignment) : null),
    // The form is remounted for a new version (Reload).
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [],
  );
  const defaultValues = useMemo(
    () =>
      original ??
      newFormValues(
        session?.subject.id ?? '',
        session ? session.participants.map((student) => student.id) : [],
      ),
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [],
  );
  const loadedVersion = useRef(assignment?.version ?? null);
  const maxScoreLocked = (assignment?.counts.graded ?? 0) > 0;
  const locked = useMemo(
    () => lockedRecipients(assignment?.roster ?? []),
    [assignment],
  );
  const schema = useMemo(
    () =>
      assignmentFormSchema({
        creating: !editing,
        today,
        nowTime,
        original,
        maxScoreLocked,
      }),
    [editing, today, nowTime, original, maxScoreLocked],
  );
  const {
    register,
    control,
    handleSubmit,
    setError: setFieldError,
    setFocus,
    formState: { errors, isDirty, isSubmitting },
  } = useForm<AssignmentFormValues>({
    resolver: zodResolver(schema),
    defaultValues,
    shouldFocusError: false,
  });
  const values = useWatch({ control }) as AssignmentFormValues;
  const [failure, setFailure] = useState<FormFailure | null>(null);
  const [studentErrors, setStudentErrors] = useState<Map<string, string>>(
    () => new Map(),
  );
  const [confirmReload, setConfirmReload] = useState(false);
  const leaving = useRef(false);
  const inFlight = useRef(false);
  const trigger = useRef<HTMLElement | null>(null);
  const headingRef = useRef<HTMLHeadingElement>(null);
  const failureRef = useRef<HTMLDivElement>(null);
  const formRef = useRef<HTMLFormElement>(null);
  const [focusTarget, setFocusTarget] = useState<
    (FocusTarget & { n: number }) | null
  >(focusHeadingOnMount ? { n: 0, to: 'heading' } : null);

  const blocker = useBlocker(
    ({ currentLocation, nextLocation }) =>
      isDirty &&
      !leaving.current &&
      `${currentLocation.pathname}${currentLocation.search}` !==
        `${nextLocation.pathname}${nextLocation.search}`,
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
      const form = formRef.current;
      let target: HTMLElement | null | undefined = null;
      switch (focusTarget.to) {
        case 'heading':
          target = headingRef.current;
          break;
        case 'failure':
          target = failureRef.current;
          break;
        case 'trigger':
          target = trigger.current?.isConnected
            ? trigger.current
            : failureRef.current;
          break;
        case 'student':
          target = form?.querySelector<HTMLElement>(
            `[data-student="${focusTarget.id}"]`,
          );
          break;
        case 'field':
          if (focusTarget.name === 'studentIds') {
            target =
              form?.querySelector<HTMLElement>(
                '[data-recipients] [data-student]:not([disabled])',
              ) ?? form?.querySelector<HTMLElement>('[data-recipients] button');
            break;
          }
          setFocus(focusTarget.name as FieldPath<AssignmentFormValues>);
          return;
      }
      (target ?? headingRef.current)?.focus();
    }, 0);
    return () => window.clearTimeout(timer);
  }, [focusTarget, setFocus]);

  /** A refused save: its fields, its students, or the failure region. */
  function refused(caught: unknown, submitted: AssignmentFormValues) {
    const refusal = classifyFormRefusal(caught);
    if (refusal === 'missing') {
      return; // The page shows the not-found page.
    }
    if (refusal === 'session') {
      setFailure({ kind: 'session' });
      focusNext({ to: 'failure' });
      return;
    }
    if (refusal === 'grades') {
      setFieldError('maxScore', {
        message: tErrors('assignment_has_grades'),
      });
    }
    if (refusal === 'recipientSubmission' && caught instanceof ApiError) {
      setStudentErrors(
        new Map(
          detailStudentIds(caught).map((studentId) => [
            studentId,
            t('assignments.form.alreadyHandedIn'),
          ]),
        ),
      );
    }
    if (refusal === 'fields') {
      const placed = placeServerErrors(caught, submitted, today);
      if (placed.fields.length > 0) {
        for (const { field, message: text } of placed.fields) {
          setFieldError(field, { message: message(text) });
        }
        setStudentErrors(
          new Map(
            [...placed.students].map(([studentId, text]) => [
              studentId,
              message(text) ?? '',
            ]),
          ),
        );
        const firstStudent = [...placed.students.keys()][0];
        const first = placed.fields[0]!.field;
        focusNext(
          first === 'studentIds' && firstStudent
            ? { to: 'student', id: firstStudent }
            : { to: 'field', name: first },
        );
        return;
      }
    }
    setFailure({
      kind: 'error',
      error: caught,
      reload: refusal !== 'other' && refusal !== 'fields',
    });
    focusNext({ to: 'failure' });
  }

  async function save(submitted: AssignmentFormValues) {
    setFailure(null);
    setStudentErrors(new Map());
    leaving.current = true;
    try {
      await onSave(submitted, original, loadedVersion.current);
    } catch (caught) {
      leaving.current = false;
      refused(caught, submitted);
    }
  }

  function invalid(found: FieldErrors<AssignmentFormValues>) {
    const first = FIELD_ORDER.find((name) => found[name]);
    if (first) {
      focusNext({ to: 'field', name: first });
    }
  }

  function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (inFlight.current) {
      return;
    }
    inFlight.current = true;
    void handleSubmit(save, invalid)().finally(() => {
      inFlight.current = false;
    });
  }

  function reload() {
    const from = document.activeElement;
    trigger.current = from instanceof HTMLElement ? from : null;
    if (isDirty) {
      setConfirmReload(true);
    } else {
      leaving.current = true;
      onReload();
    }
  }

  const field = (name: string) => `${id}-${name}`;
  const described = (...parts: (string | false | undefined)[]) =>
    parts.filter(Boolean).join(' ') || undefined;
  const fieldMessage = (name: FormField) => {
    const text = errors[name]?.message;
    return text ? (message(text) ?? text) : undefined;
  };
  const subjectMessage = fieldMessage('subjectId');
  const titleMessage = fieldMessage('title');
  const instructionsMessage = fieldMessage('instructions');
  const dueOnMessage = fieldMessage('dueOn');
  const dueTimeMessage = fieldMessage('dueTime');
  const maxScoreMessage = fieldMessage('maxScore');
  const recipientsMessage = fieldMessage('studentIds');
  const heading = editing
    ? t('assignments.form.editTitle')
    : t('assignments.form.newTitle');
  const fixedOptions: readonly PickerStudent[] | null = session
    ? session.participants
    : editing && participants
      ? participants
      : null;
  const base: PickerStudent[] = (assignment?.roster ?? []).map((row) => ({
    id: row.student.id,
    name: row.student.display_name,
  }));

  return (
    <div className="flex w-full max-w-2xl min-w-0 flex-col gap-6">
      <div className="flex min-w-0 flex-col gap-1">
        <h1
          ref={headingRef}
          tabIndex={-1}
          className="text-2xl font-semibold break-words outline-none"
        >
          {heading}
        </h1>
        {session ? (
          <p className="text-sm break-words text-muted-foreground">
            {t('assignments.form.forClass', {
              when: formatInstant(session.startsAt),
            })}
          </p>
        ) : null}
      </div>
      {failure ? (
        failure.kind === 'session' ? (
          <AssignmentFailure
            ref={failureRef}
            error={null}
            message={t('assignments.form.sessionInvalid')}
            link={{
              to: withoutSessionPath,
              label: t('assignments.form.withoutSession'),
            }}
          />
        ) : (
          <AssignmentFailure
            ref={failureRef}
            error={failure.error}
            message={
              failure.error instanceof ApiError &&
              failure.error.code === 'version_conflict'
                ? t('assignments.conflict')
                : undefined
            }
            onReload={failure.reload ? reload : undefined}
          />
        )
      ) : null}
      <form
        ref={formRef}
        method="post"
        noValidate
        onSubmit={submit}
        className="flex min-w-0 flex-col gap-6"
      >
        <div className="flex min-w-0 flex-col gap-2">
          {editing || session ? (
            <>
              <p className="text-sm font-medium">
                {t('assignments.form.subject')}
              </p>
              <p className="text-sm break-words">
                {session
                  ? t('academics.subjectOption', {
                      subject: session.subject.name,
                      level: session.subject.level,
                    })
                  : assignment?.subject.name}
              </p>
              {editing ? (
                <p className="text-sm text-muted-foreground">
                  {t('assignments.form.subjectFixed')}
                </p>
              ) : null}
            </>
          ) : (
            <>
              <label htmlFor={field('subject')} className="text-sm font-medium">
                {t('assignments.form.subject')}
              </label>
              <select
                id={field('subject')}
                className={SELECT_CLASS}
                aria-invalid={subjectMessage ? true : undefined}
                aria-describedby={described(
                  subjectMessage && field('subject-error'),
                )}
                {...register('subjectId')}
              >
                <option value="">{t('assignments.form.chooseSubject')}</option>
                {subjects.map((subject) => (
                  <option key={subject.id} value={subject.id}>
                    {subject.label}
                  </option>
                ))}
              </select>
              <FieldError
                id={field('subject-error')}
                message={subjectMessage}
              />
            </>
          )}
        </div>
        <div className="flex min-w-0 flex-col gap-2">
          <label htmlFor={field('title')} className="text-sm font-medium">
            {t('assignments.form.title')}
          </label>
          <Input
            id={field('title')}
            autoComplete="off"
            aria-invalid={titleMessage ? true : undefined}
            aria-describedby={described(titleMessage && field('title-error'))}
            {...register('title')}
          />
          <FieldError id={field('title-error')} message={titleMessage} />
        </div>
        <div className="flex min-w-0 flex-col gap-2">
          <label
            htmlFor={field('instructions')}
            className="text-sm font-medium"
          >
            {t('assignments.form.instructions')}
          </label>
          <textarea
            id={field('instructions')}
            rows={5}
            className={TEXTAREA_CLASS}
            aria-invalid={instructionsMessage ? true : undefined}
            aria-describedby={described(
              instructionsMessage && field('instructions-error'),
              field('instructions-counter'),
            )}
            {...register('instructions')}
          />
          <FieldError
            id={field('instructions-error')}
            message={instructionsMessage}
          />
          <Counter
            id={field('instructions-counter')}
            value={values.instructions ?? ''}
            max={INSTRUCTIONS_MAX}
          />
        </div>
        <div className="grid min-w-0 grid-cols-1 gap-4 sm:grid-cols-2">
          <div className="flex min-w-0 flex-col gap-2">
            <label htmlFor={field('due-on')} className="text-sm font-medium">
              {t('assignments.form.dueOn')}
            </label>
            <Input
              id={field('due-on')}
              type="date"
              min={today}
              max={latestDueOn(today)}
              aria-invalid={dueOnMessage ? true : undefined}
              aria-describedby={described(
                dueOnMessage && field('due-on-error'),
              )}
              {...register('dueOn')}
            />
            <FieldError id={field('due-on-error')} message={dueOnMessage} />
          </div>
          <div className="flex min-w-0 flex-col gap-2">
            <label htmlFor={field('due-time')} className="text-sm font-medium">
              {t('assignments.form.dueTime')}
            </label>
            <Input
              id={field('due-time')}
              type="time"
              aria-invalid={dueTimeMessage ? true : undefined}
              aria-describedby={described(
                field('due-time-help'),
                dueTimeMessage && field('due-time-error'),
              )}
              {...register('dueTime')}
            />
            <p
              id={field('due-time-help')}
              className="text-sm text-muted-foreground"
            >
              {t('assignments.form.dueTimeHelp')}
            </p>
            <FieldError id={field('due-time-error')} message={dueTimeMessage} />
          </div>
        </div>
        <div className="flex min-w-0 flex-col gap-2">
          <label htmlFor={field('max-score')} className="text-sm font-medium">
            {t('assignments.form.maxScore')}
          </label>
          <Input
            id={field('max-score')}
            inputMode="decimal"
            autoComplete="off"
            className="max-w-40"
            disabled={maxScoreLocked}
            aria-invalid={maxScoreMessage ? true : undefined}
            aria-describedby={described(
              field('max-score-help'),
              maxScoreMessage && field('max-score-error'),
            )}
            {...register('maxScore')}
          />
          <p
            id={field('max-score-help')}
            className="text-sm text-muted-foreground"
          >
            {maxScoreLocked
              ? t('assignments.form.maxScoreLocked')
              : t('assignments.form.maxScoreHelp')}
          </p>
          <FieldError id={field('max-score-error')} message={maxScoreMessage} />
        </div>
        <Controller
          control={control}
          name="studentIds"
          render={({ field: recipients }) => (
            <RecipientPicker
              idPrefix={field('recipients')}
              value={recipients.value}
              onChange={(next) => {
                recipients.onChange(next);
                setStudentErrors(new Map());
              }}
              fixed={fixedOptions}
              base={base}
              locked={locked}
              error={recipientsMessage}
              studentErrors={studentErrors}
              note={editing ? t('assignments.form.addedNotified') : undefined}
            />
          )}
        />
        <div className="flex flex-col items-start gap-3 border-t border-border pt-4">
          <Button type="submit" disabled={isSubmitting}>
            {isSubmitting ? (
              <Spinner label={t('assignments.form.saving')} showLabel />
            ) : (
              t('assignments.form.save')
            )}
          </Button>
        </div>
      </form>
      <ConfirmDialog
        open={confirmReload}
        title={t('assignments.reloadConfirm.title')}
        description={t('assignments.reloadConfirm.description')}
        confirmLabel={t('assignments.reloadConfirm.confirm')}
        busyLabel={t('assignments.busy')}
        cancelLabel={t('assignments.cancel')}
        destructive
        onCancel={() => {
          setConfirmReload(false);
          focusNext({ to: 'trigger' });
        }}
        onConfirm={() => {
          setConfirmReload(false);
          leaving.current = true;
          onReload();
          return Promise.resolve();
        }}
      />
      <LeaveDialog blocker={blocker} />
    </div>
  );
}
