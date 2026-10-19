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
import { SearchPicker } from '@/features/guardians/SearchPicker';
import { listName } from '@/features/guardians/rules';
import { FAMILIES_QUERY_KEY, type MemberKind } from './api';

// The "Add student" / "Add guardian" dialog of the family detail (#172 H3),
// with the #34 picker pattern: a search field whose results are a native
// radio group. `family_member_conflict` shows inside the dialog as "This
// student (guardian) already belongs to another family. Remove them from it
// first."; any other error shows its own message. Focus is trapped while it
// is open and returns to the button that opened it.

/** A picker result: a student or a guardian list item. */
export interface MemberOption {
  id: string;
  given_names: string;
  family_names: string;
}

interface AddMemberDialogProps<T extends MemberOption> {
  kind: MemberKind;
  open: boolean;
  /** The search (GET /students?q= or GET /guardians?status=active&q=). */
  search: (q: string, signal: AbortSignal) => Promise<T[]>;
  /** Adds the chosen member; rejects with the error to show. */
  onAdd: (memberId: string) => Promise<void>;
  onCancel: () => void;
}

export function AddMemberDialog<T extends MemberOption>({
  kind,
  open,
  search,
  onAdd,
  onCancel,
}: AddMemberDialogProps<T>) {
  const { t } = useTranslation();
  const returnFocus = useRef<Element | null>(null);
  const sending = useRef(false);
  const [busy, setBusy] = useState(false);
  const [chosen, setChosen] = useState<T | null>(null);
  const [missing, setMissing] = useState(false);
  const [error, setError] = useState<unknown>(null);
  const copy =
    kind === 'students'
      ? {
          title: t('families.students.addTitle'),
          body: t('families.students.addBody'),
          label: t('families.students.student'),
          choose: t('families.students.choose'),
          conflict: t('families.students.conflict'),
        }
      : {
          title: t('families.guardians.addTitle'),
          body: t('families.guardians.addBody'),
          label: t('families.guardians.guardian'),
          choose: t('families.guardians.choose'),
          conflict: t('families.guardians.conflict'),
        };

  function reset() {
    setChosen(null);
    setMissing(false);
    setError(null);
  }

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (sending.current) {
      return;
    }
    setMissing(chosen === null);
    if (!chosen) {
      return;
    }
    sending.current = true;
    setBusy(true);
    setError(null);
    try {
      await onAdd(chosen.id);
      reset();
    } catch (caught) {
      setError(caught);
    } finally {
      sending.current = false;
      setBusy(false);
    }
  }

  const conflict =
    error instanceof ApiError && error.code === 'family_member_conflict';

  return (
    <Dialog
      open={open}
      onOpenChange={(next) => {
        if (!next && !sending.current) {
          reset();
          onCancel();
        }
      }}
    >
      {open ? (
        <DialogContent
          onOpenAutoFocus={() => {
            returnFocus.current = document.activeElement;
          }}
          onCloseAutoFocus={(event) => {
            event.preventDefault();
            const target = returnFocus.current;
            if (target instanceof HTMLElement && target.isConnected) {
              target.focus();
            }
          }}
          onInteractOutside={(event) => event.preventDefault()}
        >
          <DialogHeader>
            <DialogTitle>{copy.title}</DialogTitle>
            <DialogDescription>{copy.body}</DialogDescription>
          </DialogHeader>
          <form
            noValidate
            onSubmit={(event) => void submit(event)}
            className="flex flex-col gap-4"
          >
            {error ? (
              <ErrorAlert
                error={error}
                message={conflict ? copy.conflict : undefined}
              />
            ) : null}
            <SearchPicker<T>
              label={copy.label}
              queryKey={[...FAMILIES_QUERY_KEY, 'member-options', kind]}
              search={search}
              optionId={(item) => item.id}
              optionLabel={(item) => listName(item)}
              selectedId={chosen?.id ?? null}
              onSelect={(item) => {
                setChosen(item);
                setMissing(false);
              }}
              errorMessage={missing ? copy.choose : undefined}
              autoFocus
            />
            <DialogFooter>
              <Button
                type="button"
                variant="outline"
                disabled={busy}
                onClick={() => {
                  reset();
                  onCancel();
                }}
              >
                {t('families.cancel')}
              </Button>
              <Button type="submit" disabled={busy}>
                {busy ? (
                  <Spinner label={t('families.saving')} showLabel />
                ) : (
                  t('families.addAction')
                )}
              </Button>
            </DialogFooter>
          </form>
        </DialogContent>
      ) : null}
    </Dialog>
  );
}
