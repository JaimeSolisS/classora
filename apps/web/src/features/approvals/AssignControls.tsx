import { useId, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { ErrorAlert } from '@/components/feedback/ErrorAlert';
import { Button } from '@/components/ui/button';
import type { AccountName, ApprovalRequestItem } from './api';

// The assignment controls of an open request in the detail panel (M8-01b
// C1): "Assign to me" (hidden when the caller is already the assignee), an
// "Assign to…" select of the people who can review the type (loaded when the
// panel opens, the current assignee preselected) with its "Assign" button,
// and "Remove assignment" when assigned. The select never sends on change:
// with the keyboard, each arrow press would be a request. Presentational:
// the page sends `POST …/assign` and passes the outcome back.

export type AssigneeOptionsState =
  | { status: 'loading' }
  | { status: 'error'; error: unknown; onRetry: () => void }
  | { status: 'ready'; items: AccountName[] };

interface AssignControlsProps {
  item: ApprovalRequestItem;
  /** The signed-in user's id (hides "Assign to me" for the assignee). */
  currentUserId: string | null;
  options: AssigneeOptionsState;
  /** True while an assignment is sent: every control is disabled. */
  assigning: boolean;
  /** Sends the assignment; null unassigns. */
  onAssign: (assigneeId: string | null) => void;
}

const SELECT_CLASS =
  'min-h-11 w-full rounded-md border border-input bg-background px-2.5 text-base outline-none focus-visible:border-ring focus-visible:ring-3 focus-visible:ring-ring disabled:cursor-not-allowed disabled:opacity-50 md:text-sm';

export function AssignControls({
  item,
  currentUserId,
  options,
  assigning,
  onAssign,
}: AssignControlsProps) {
  const { t } = useTranslation();
  const id = useId();
  const current = item.assignee?.id ?? '';
  // The chosen person, reset to the current assignee when it changes.
  const [chosen, setChosen] = useState(current);
  const [shownFor, setShownFor] = useState(current);
  if (shownFor !== current) {
    setShownFor(current);
    setChosen(current);
  }
  const mine = currentUserId !== null && current === currentUserId;
  const items = options.status === 'ready' ? options.items : [];
  const listed = items.some((person) => person.id === chosen);

  return (
    <div className="flex flex-col gap-3">
      {mine ? null : (
        <Button
          type="button"
          variant="outline"
          className="min-h-11 self-start"
          disabled={assigning}
          onClick={() => currentUserId && onAssign(currentUserId)}
        >
          {t('approvals.assign.toMe')}
        </Button>
      )}
      <div className="flex flex-col gap-2">
        <label htmlFor={`${id}-assignee`} className="text-sm font-medium">
          {t('approvals.assign.toLabel')}
        </label>
        <div className="flex flex-col gap-2 sm:flex-row">
          <select
            id={`${id}-assignee`}
            value={listed ? chosen : ''}
            className={SELECT_CLASS}
            disabled={assigning || options.status !== 'ready'}
            aria-describedby={
              options.status === 'loading' ? `${id}-loading` : undefined
            }
            onChange={(event) => setChosen(event.target.value)}
          >
            <option value="">{t('approvals.assign.choose')}</option>
            {items.map((person) => (
              <option key={person.id} value={person.id}>
                {person.display_name}
              </option>
            ))}
          </select>
          <Button
            type="button"
            className="min-h-11"
            disabled={assigning || !listed || chosen === current}
            onClick={() => onAssign(chosen)}
          >
            {t('approvals.assign.submit')}
          </Button>
        </div>
        {options.status === 'loading' ? (
          <p id={`${id}-loading`} className="text-sm text-muted-foreground">
            {t('approvals.assign.loadingOptions')}
          </p>
        ) : null}
        {options.status === 'error' ? (
          <ErrorAlert error={options.error} onRetry={options.onRetry} />
        ) : null}
      </div>
      {current ? (
        <Button
          type="button"
          variant="outline"
          className="min-h-11 self-start"
          disabled={assigning}
          onClick={() => onAssign(null)}
        >
          {t('approvals.assign.remove')}
        </Button>
      ) : null}
    </div>
  );
}
