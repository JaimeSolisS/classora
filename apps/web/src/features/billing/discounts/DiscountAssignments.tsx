import { useId, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { ErrorAlert } from '@/components/feedback/ErrorAlert';
import { Button } from '@/components/ui/button';
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from '@/components/ui/table';
import { ConfirmDialog } from '@/features/guardians/ConfirmDialog';
import { useFormatters } from '@/lib/formatters';
import type { TargetPickerProps } from '../TargetPicker';
import type { TargetOption } from '../targets';
import type { DiscountAssignment, DiscountDetail } from './api';
import { AssignDialog } from './AssignDialog';

// A discount's assignments (#367 D2). For `assigned` discounts: the
// students and families (name, type, assigned date) with "Remove" behind a
// confirmation, and "Assign". For `all` discounts: who qualifies instead.
// Presentational: the page calls the API and refreshes.

export interface DiscountAssignmentsProps {
  discount: DiscountDetail;
  /** Assigns a student or a family; rejects with the error to show. */
  onAssign: (target: TargetOption) => Promise<void>;
  /** Removes an assignment; rejects with the error to show. */
  onRemove: (assignment: DiscountAssignment) => Promise<void>;
  /** Stories: the picker's search instead of the API. */
  search?: TargetPickerProps['search'];
}

function targetOf(assignment: DiscountAssignment): TargetOption | null {
  if (assignment.student) {
    return { kind: 'student', ...assignment.student };
  }
  if (assignment.family) {
    return { kind: 'family', ...assignment.family };
  }
  return null;
}

export function DiscountAssignments({
  discount,
  onAssign,
  onRemove,
  search,
}: DiscountAssignmentsProps) {
  const { t } = useTranslation();
  const { formatInstant } = useFormatters();
  const id = useId();
  const ids = { title: `${id}-title` };
  const [assigning, setAssigning] = useState(false);
  const [removing, setRemoving] = useState<DiscountAssignment | null>(null);
  const [removeError, setRemoveError] = useState<unknown>(null);
  const [announcement, setAnnouncement] = useState('');

  if (discount.audience === 'all') {
    return (
      <section aria-labelledby={ids.title} className="flex flex-col gap-2">
        <h2 id={ids.title} className="text-lg font-semibold">
          {t('billing.discounts.assignments.title')}
        </h2>
        <p>{t('billing.discounts.assignments.everyone')}</p>
      </section>
    );
  }

  const assignedIds = new Set(
    discount.assignments.flatMap((assignment) => {
      const target = targetOf(assignment);
      return target ? [target.id] : [];
    }),
  );
  const removingTarget = removing ? targetOf(removing) : null;

  async function confirmRemove() {
    if (!removing) {
      return;
    }
    setRemoveError(null);
    try {
      await onRemove(removing);
      setRemoving(null);
      setAnnouncement(t('billing.discounts.assignments.removed'));
    } catch (caught) {
      setRemoveError(caught);
    }
  }

  return (
    <section aria-labelledby={ids.title} className="flex flex-col gap-4">
      <div className="flex flex-wrap items-center justify-between gap-4">
        <h2 id={ids.title} className="text-lg font-semibold">
          {t('billing.discounts.assignments.title')}
        </h2>
        <Button
          type="button"
          onClick={() => {
            setAnnouncement('');
            setAssigning(true);
          }}
        >
          {t('billing.discounts.assign.title')}
        </Button>
      </div>
      <p role="status" className="text-sm">
        {announcement}
      </p>
      {discount.assignments.length === 0 ? (
        <p>{t('billing.discounts.assignments.empty')}</p>
      ) : (
        <Table aria-labelledby={ids.title}>
          <TableHeader>
            <TableRow>
              <TableHead scope="col">
                {t('billing.discounts.assignments.name')}
              </TableHead>
              <TableHead scope="col">
                {t('billing.discounts.assignments.type')}
              </TableHead>
              <TableHead scope="col">
                {t('billing.discounts.assignments.assignedOn')}
              </TableHead>
              <TableHead scope="col">
                <span className="sr-only">
                  {t('billing.discounts.columns.actions')}
                </span>
              </TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {discount.assignments.map((assignment) => {
              const target = targetOf(assignment);
              const name = target?.name ?? '';
              return (
                <TableRow
                  key={assignment.id}
                  data-assignment-id={assignment.id}
                >
                  <TableCell className="max-w-xs whitespace-normal break-words">
                    {name}
                  </TableCell>
                  <TableCell>
                    {target ? t(`billing.targets.kinds.${target.kind}`) : null}
                  </TableCell>
                  <TableCell>
                    {formatInstant(assignment.created_at, 'date')}
                  </TableCell>
                  <TableCell className="text-right">
                    <Button
                      type="button"
                      variant="outline"
                      size="sm"
                      aria-label={t('billing.discounts.assignments.removeFor', {
                        name,
                      })}
                      onClick={() => {
                        setAnnouncement('');
                        setRemoveError(null);
                        setRemoving(assignment);
                      }}
                    >
                      {t('billing.discounts.assignments.remove')}
                    </Button>
                  </TableCell>
                </TableRow>
              );
            })}
          </TableBody>
        </Table>
      )}
      <AssignDialog
        open={assigning}
        discountName={discount.name}
        assignedIds={assignedIds}
        search={search}
        onAssign={async (target) => {
          await onAssign(target);
          setAssigning(false);
          setAnnouncement(
            t('billing.discounts.assign.done', { name: target.name }),
          );
        }}
        onCancel={() => setAssigning(false)}
      />
      <ConfirmDialog
        open={removing !== null}
        title={t('billing.discounts.assignments.removeTitle')}
        body={t('billing.discounts.assignments.removeBody', {
          name: removingTarget?.name ?? '',
          discount: discount.name,
        })}
        confirmLabel={t('billing.discounts.assignments.remove')}
        destructive
        alert={removeError ? <ErrorAlert error={removeError} /> : null}
        onConfirm={confirmRemove}
        onCancel={() => {
          setRemoving(null);
          setRemoveError(null);
        }}
      />
    </section>
  );
}
