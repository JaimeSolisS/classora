import { useId, useRef, useState, type FormEvent, type ReactNode } from 'react';
import { useTranslation } from 'react-i18next';
import { ApiError } from '@/app/api';
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
import { SearchPicker } from '@/features/guardians/SearchPicker';
import {
  conflictsOf,
  type Option,
  type SchedulingConflict,
} from '@/features/scheduling/create/api';
import {
  sessionOverride,
  type SessionOverride,
} from '@/features/scheduling/create/rules';
import {
  useFieldMessage,
  useReturnFocus,
} from '@/features/scheduling/series/hooks';
import { SeriesOverrideSection } from '@/features/scheduling/series/SeriesOverrideSection';
import type { GroupEnrollmentCreated } from './api';
import { enrollAccepts, type GroupMessage } from './rules';

// "Enroll student" (#234 D1, D2): a search over active students (the ones
// already enrolled are listed disabled, "already enrolled"). A 201 closes
// the dialog (the page refreshes and announces it). A 409
// scheduling_conflict shows the date-grouped panel inside the dialog: the
// group's own conflicts first ("Whole group"), then each occurrence's by
// local date, with the #49 labels (group_capacity_exceeded "Can be
// overridden"; room_capacity_exceeded and student_overlap "Not allowed").
// A schedule.override holder with only soft conflicts gets "Override and
// enroll" (the #49 dialog, reason 1-1000), which resends `{student_id,
// override: {codes, reason}}`; a new 409 replaces the list. already_enrolled,
// group_ended and the student's 422 show translated messages.
//
// A cohort enrollment (#245) reuses it with its own texts (`labels`), a
// help line under the description (`help`), "Can't be overridden" on the
// hard conflicts and, for anyone without schedule.override, "Needs an
// override (Owner)" on the soft ones.

/** Texts for another write than a group's (#245); default: the group's. */
export interface EnrollDialogLabels {
  title: string;
  description: string;
  studentHelp: string;
  alreadyEnrolled: (name: string) => string;
  submit: string;
  submitting: string;
  conflictsHeading: (count: number) => string;
  overrideAction: string;
  overrideDescription: string;
  noDateHeading: string;
  /** A soft code the write doesn't accept (holders of schedule.override). */
  notYetLabel: string;
  notYet: string;
  /** The label of a hard conflict (default: "Not allowed"). */
  hardLabel?: string;
  /** The label of a soft conflict for a caller without schedule.override. */
  needsOverrideLabel?: string;
}

export interface EnrollDialogProps {
  open: boolean;
  groupName: string;
  /** The students enrolled now (disabled in the picker). */
  enrolledIds: ReadonlySet<string>;
  searchStudents: (q: string, signal: AbortSignal) => Promise<Option[]>;
  canOverride: boolean;
  onEnroll: (
    student: Option,
    override?: SessionOverride,
  ) => Promise<GroupEnrollmentCreated>;
  /** After a 201: the page refreshes and announces it. */
  onDone: (created: GroupEnrollmentCreated, student: Option) => void;
  onCancel: () => void;
  /** Texts for another write (#245); default: the group's. */
  labels?: EnrollDialogLabels;
  /** A help line under the description (#245 B2). */
  help?: ReactNode;
  /** Stories: a chosen student and a 409 to start from. */
  initialStudent?: Option;
  initialConflicts?: SchedulingConflict[];
  initialReason?: string;
}

export function EnrollDialog(props: EnrollDialogProps) {
  const busy = useRef(false);
  const { t } = useTranslation();
  const returnFocus = useReturnFocus();
  return (
    <Dialog
      open={props.open}
      onOpenChange={(open) => {
        if (!open && !busy.current) {
          props.onCancel();
        }
      }}
    >
      {props.open ? (
        <DialogContent
          {...returnFocus}
          onOpenAutoFocus={(event) => {
            // Record the opener first, then start in the student search.
            // Never `autoFocus` the search: it would take the focus before
            // this runs and the opener would be lost (#234 QA D1).
            returnFocus.onOpenAutoFocus();
            event.preventDefault();
            const content = event.currentTarget;
            if (content instanceof HTMLElement) {
              content.querySelector('input')?.focus();
            }
          }}
          onInteractOutside={(event) => event.preventDefault()}
          className="max-h-[calc(100dvh-2rem)] overflow-y-auto sm:max-w-xl"
        >
          <DialogHeader>
            <DialogTitle>
              {props.labels?.title ?? t('groups.enroll.title')}
            </DialogTitle>
            <DialogDescription>
              {props.labels?.description ??
                t('groups.enroll.description', { group: props.groupName })}
            </DialogDescription>
            {props.help ? <p className="text-sm">{props.help}</p> : null}
          </DialogHeader>
          <EnrollContent {...props} busyRef={busy} />
        </DialogContent>
      ) : null}
    </Dialog>
  );
}

function EnrollContent({
  enrolledIds,
  searchStudents,
  canOverride,
  onEnroll,
  onDone,
  onCancel,
  initialStudent,
  initialConflicts,
  initialReason,
  groupName,
  labels: custom,
  busyRef,
}: EnrollDialogProps & { busyRef: { current: boolean } }) {
  const { t } = useTranslation();
  const labels: EnrollDialogLabels = custom ?? {
    title: t('groups.enroll.title'),
    description: t('groups.enroll.description', { group: groupName }),
    studentHelp: t('groups.enroll.studentHelp'),
    alreadyEnrolled: (name) => t('groups.enroll.alreadyEnrolled', { name }),
    submit: t('groups.enroll.submit'),
    submitting: t('groups.enroll.submitting'),
    conflictsHeading: (count) => t('groups.enroll.conflictsHeading', { count }),
    overrideAction: t('groups.enroll.overrideAction'),
    overrideDescription: t('groups.enroll.overrideDescription'),
    noDateHeading: t('groups.conflicts.wholeGroup'),
    notYetLabel: t('groups.conflicts.notYetLabel'),
    notYet: t('groups.conflicts.notYet'),
  };
  // Without schedule.override, a write with its own label says each soft
  // conflict needs an override (#245 B3); the group's keeps "Can be
  // overridden" and the "Only an Owner…" text under the panel.
  const needsOverride = !canOverride && labels.needsOverrideLabel;
  const message = useFieldMessage();
  const id = useId();
  const [student, setStudent] = useState<Option | null>(initialStudent ?? null);
  const [studentError, setStudentError] = useState<GroupMessage | undefined>(
    undefined,
  );
  const [conflicts, setConflicts] = useState<SchedulingConflict[] | null>(
    initialConflicts ?? null,
  );
  const [failure, setFailure] = useState<unknown>(null);
  const [submitting, setSubmitting] = useState(false);

  function handleFailure(caught: unknown) {
    const list = conflictsOf(caught);
    if (list) {
      setConflicts(list);
      return;
    }
    setConflicts(null);
    const onStudent =
      caught instanceof ApiError
        ? caught.fieldErrors.find(({ path }) => path.includes('student_id'))
        : undefined;
    if (onStudent) {
      setStudentError(
        onStudent.code === 'student_inactive' ||
          onStudent.code === 'student_unknown'
          ? `errors:${onStudent.code}`
          : 'errors:validation_error',
      );
      document.getElementById(`${id}-picker`)?.querySelector('input')?.focus();
      return;
    }
    setFailure(caught);
  }

  async function send() {
    setFailure(null);
    setStudentError(undefined);
    if (!student) {
      setStudentError('groups.enroll.studentRequired');
      document.getElementById(`${id}-picker`)?.querySelector('input')?.focus();
      return;
    }
    setConflicts(null);
    busyRef.current = true;
    setSubmitting(true);
    try {
      const created = await onEnroll(student);
      onDone(created, student);
    } catch (caught) {
      handleFailure(caught);
    } finally {
      busyRef.current = false;
      setSubmitting(false);
    }
  }

  async function override(reason: string) {
    if (!student || !conflicts) {
      return;
    }
    busyRef.current = true;
    try {
      const created = await onEnroll(
        student,
        sessionOverride(conflicts, reason),
      );
      onDone(created, student);
    } finally {
      busyRef.current = false;
    }
  }

  function onSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!busyRef.current) {
      void send();
    }
  }

  return (
    <div className="flex min-w-0 flex-col gap-4">
      {conflicts ? (
        <SeriesOverrideSection
          conflicts={conflicts}
          heading={labels.conflictsHeading(conflicts.length)}
          canOverride={canOverride}
          accepts={needsOverride ? () => false : enrollAccepts}
          onOverride={override}
          onRefused={handleFailure}
          initialReason={initialReason}
          labels={{
            action: labels.overrideAction,
            submitting: labels.submitting,
            description: labels.overrideDescription,
            noDateHeading: labels.noDateHeading,
            noOverrideLabel: needsOverride || labels.notYetLabel,
            notYet: labels.notYet,
            hardLabel: labels.hardLabel,
          }}
        />
      ) : null}
      {failure ? <ErrorAlert error={failure} /> : null}
      <form
        noValidate
        onSubmit={onSubmit}
        className="flex min-w-0 flex-col gap-4"
      >
        <div id={`${id}-picker`}>
          <SearchPicker<Option>
            label={t('groups.enroll.student')}
            help={
              student
                ? t('scheduling.studentChosen', { name: student.name })
                : labels.studentHelp
            }
            queryKey={['groups', 'enroll', 'students']}
            search={searchStudents}
            optionId={(option) => option.id}
            optionLabel={(option) =>
              enrolledIds.has(option.id)
                ? labels.alreadyEnrolled(option.name)
                : option.name
            }
            optionDisabled={(option) => enrolledIds.has(option.id)}
            selectedId={student?.id ?? null}
            onSelect={(option) => {
              setStudent(option);
              setStudentError(undefined);
              setConflicts(null);
            }}
            errorMessage={message(studentError)}
          />
        </div>
        <DialogFooter>
          <Button
            type="button"
            variant="outline"
            disabled={submitting}
            onClick={onCancel}
          >
            {t('groups.cancel')}
          </Button>
          <Button type="submit" disabled={submitting}>
            {submitting ? (
              <Spinner label={labels.submitting} showLabel />
            ) : (
              labels.submit
            )}
          </Button>
        </DialogFooter>
      </form>
    </div>
  );
}
