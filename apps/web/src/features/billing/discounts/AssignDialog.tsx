import { useRef, useState, type FormEvent } from 'react';
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
import { useReturnFocus } from '@/features/scheduling/series/hooks';
import { TargetPicker, type TargetPickerProps } from '../TargetPicker';
import type { TargetKind, TargetOption } from '../targets';

// "Assign" (#367 D2): a Student / Family radio and the search picker. The
// students and families already assigned are listed disabled; a 409
// discount_assignment_exists (someone assigned it meanwhile) shows inside
// the dialog. Focus returns to the button that opened it.

export interface AssignDialogProps {
  open: boolean;
  discountName: string;
  /** The ids of the students and families assigned now. */
  assignedIds: ReadonlySet<string>;
  /** Assigns the target; rejects with the error to show. */
  onAssign: (target: TargetOption) => Promise<void>;
  onCancel: () => void;
  /** Stories: the search instead of the API. */
  search?: TargetPickerProps['search'];
}

export function AssignDialog(props: AssignDialogProps) {
  const busy = useRef(false);
  const returnFocus = useReturnFocus();
  const { t } = useTranslation();
  return (
    <Dialog
      open={props.open}
      onOpenChange={(next) => {
        if (!next && !busy.current) {
          props.onCancel();
        }
      }}
    >
      {props.open ? (
        <DialogContent
          {...returnFocus}
          onInteractOutside={(event) => event.preventDefault()}
          className="max-h-[calc(100dvh-2rem)] overflow-y-auto sm:max-w-xl"
        >
          <DialogHeader>
            <DialogTitle>{t('billing.discounts.assign.title')}</DialogTitle>
            <DialogDescription>
              {t('billing.discounts.assign.description', {
                name: props.discountName,
              })}
            </DialogDescription>
          </DialogHeader>
          <AssignForm {...props} busyRef={busy} />
        </DialogContent>
      ) : null}
    </Dialog>
  );
}

function AssignForm({
  assignedIds,
  onAssign,
  onCancel,
  search,
  busyRef,
}: AssignDialogProps & { busyRef: { current: boolean } }) {
  const { t } = useTranslation();
  const [kind, setKind] = useState<TargetKind>('student');
  const [target, setTarget] = useState<TargetOption | null>(null);
  const [missing, setMissing] = useState(false);
  const [failure, setFailure] = useState<unknown>(null);
  const [submitting, setSubmitting] = useState(false);

  async function send() {
    setFailure(null);
    setMissing(target === null);
    if (!target) {
      return;
    }
    busyRef.current = true;
    setSubmitting(true);
    try {
      await onAssign(target);
    } catch (caught) {
      setFailure(caught);
    } finally {
      busyRef.current = false;
      setSubmitting(false);
    }
  }

  function onSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!busyRef.current) {
      void send();
    }
  }

  const exists =
    failure instanceof ApiError &&
    failure.code === 'discount_assignment_exists';

  return (
    <form
      noValidate
      onSubmit={onSubmit}
      className="flex min-w-0 flex-col gap-4"
    >
      {failure ? (
        <ErrorAlert
          error={failure}
          message={exists ? t('billing.discounts.assign.exists') : undefined}
        />
      ) : null}
      <TargetPicker
        legend={t('billing.discounts.assign.kind')}
        kind={kind}
        onKind={(next) => {
          setKind(next);
          setTarget(null);
          setFailure(null);
        }}
        selected={target}
        onSelect={(next) => {
          setTarget(next);
          setMissing(false);
          setFailure(null);
        }}
        disabledIds={assignedIds}
        disabledLabel={(name) =>
          t('billing.discounts.assign.alreadyAssigned', { name })
        }
        errorMessage={
          missing ? t(`billing.discounts.assign.choose.${kind}`) : undefined
        }
        search={search}
      />
      <DialogFooter>
        <Button
          type="button"
          variant="outline"
          disabled={submitting}
          onClick={onCancel}
        >
          {t('billing.discounts.cancel')}
        </Button>
        <Button type="submit" disabled={submitting}>
          {submitting ? (
            <Spinner label={t('billing.discounts.saving')} showLabel />
          ) : (
            t('billing.discounts.assign.submit')
          )}
        </Button>
      </DialogFooter>
    </form>
  );
}
