import { useState } from 'react';
import { useTranslation } from 'react-i18next';
import { ErrorAlert } from '@/components/feedback/ErrorAlert';
import { LoadingState } from '@/components/feedback/LoadingState';
import { Button } from '@/components/ui/button';
import {
  Table,
  TableBody,
  TableCaption,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from '@/components/ui/table';
import { ConfirmDialog } from '@/features/guardians/ConfirmDialog';
import type { PayItemType } from './api';
import { PayrollTabs } from './PayrollTabs';

// The types tab (#454 A2, A4), without any query: name, direction and state, a
// table from `md` up and cards below, in the API's order (active first). The
// add, edit and deactivate controls are for payroll.manage holders.
// Deactivating and reactivating sit behind a confirmation, which stays open
// with the refusal when `onSetActive` rejects.

export type PayItemTypesState =
  | { status: 'loading' }
  | { status: 'error'; error: unknown; onRetry: () => void }
  | {
      status: 'ready';
      types: readonly PayItemType[];
      /** A refetch failed: the rows may be out of date (#458 C2). */
      refreshError?: unknown;
      onRefresh?: () => void;
    };

export interface PayItemTypesViewProps {
  state: PayItemTypesState;
  canManage: boolean;
  onAdd: () => void;
  onEdit: (type: PayItemType) => void;
  /** Rejects with the error to show in the confirmation. */
  onSetActive: (type: PayItemType, active: boolean) => Promise<void>;
  announcement?: string;
  /** Stories: the type whose confirmation starts open, and its refusal. */
  initialToggle?: PayItemType;
  initialToggleError?: unknown;
}

const NAME_CLASS = '[overflow-wrap:anywhere]';

function TypeActions({
  type,
  onEdit,
  onToggle,
}: {
  type: PayItemType;
  onEdit: (type: PayItemType) => void;
  onToggle: (type: PayItemType) => void;
}) {
  const { t } = useTranslation();
  const name = { name: type.name };
  return (
    <div className="flex flex-wrap gap-2">
      <Button
        type="button"
        variant="outline"
        size="sm"
        className="min-h-11 md:min-h-8"
        aria-label={t('payroll.payItemTypes.editFor', name)}
        onClick={() => onEdit(type)}
      >
        {t('payroll.payItemTypes.edit')}
      </Button>
      <Button
        type="button"
        variant="outline"
        size="sm"
        className="min-h-11 md:min-h-8"
        aria-label={
          type.is_active
            ? t('payroll.payItemTypes.deactivateFor', name)
            : t('payroll.payItemTypes.reactivateFor', name)
        }
        onClick={() => onToggle(type)}
      >
        {type.is_active
          ? t('payroll.payItemTypes.deactivate')
          : t('payroll.payItemTypes.reactivate')}
      </Button>
    </div>
  );
}

export function PayItemTypesView({
  state,
  canManage,
  onAdd,
  onEdit,
  onSetActive,
  announcement = '',
  initialToggle,
  initialToggleError = null,
}: PayItemTypesViewProps) {
  const { t } = useTranslation();
  const [toggling, setToggling] = useState<PayItemType | null>(
    initialToggle ?? null,
  );
  const [toggleError, setToggleError] = useState<unknown>(initialToggleError);

  function startToggle(type: PayItemType) {
    setToggleError(null);
    setToggling(type);
  }

  async function confirmToggle() {
    if (!toggling) {
      return;
    }
    setToggleError(null);
    try {
      await onSetActive(toggling, !toggling.is_active);
      setToggling(null);
    } catch (caught) {
      setToggleError(caught);
    }
  }

  const stateWord = (type: PayItemType) =>
    t(`payroll.payItemTypes.states.${type.is_active ? 'active' : 'inactive'}`);

  return (
    <div className="flex min-w-0 flex-col gap-8">
      <PayrollTabs />
      <div className="flex min-w-0 flex-col gap-4">
        <div className="flex flex-wrap items-center justify-between gap-4">
          <h1 className="text-2xl font-semibold">
            {t('payroll.payItemTypes.title')}
          </h1>
          {canManage ? (
            <Button
              type="button"
              className="min-h-11 md:min-h-9"
              onClick={onAdd}
            >
              {t('payroll.payItemTypes.add')}
            </Button>
          ) : null}
        </div>
        <p className="text-sm text-muted-foreground">
          {t('payroll.payItemTypes.intro')}
        </p>
        {state.status === 'loading' ? <LoadingState /> : null}
        {state.status === 'error' ? (
          <ErrorAlert error={state.error} onRetry={state.onRetry} />
        ) : null}
        {state.status === 'ready' && state.refreshError ? (
          <ErrorAlert
            error={state.refreshError}
            message={t('payroll.payItemTypes.stale')}
            onRetry={state.onRefresh}
            retryLabel={t('payroll.payItemTypes.reloadList')}
          />
        ) : null}
        {state.status === 'ready' && state.types.length === 0 ? (
          <p data-pay-types-empty>
            {canManage
              ? t('payroll.payItemTypes.emptyManage')
              : t('payroll.payItemTypes.empty')}
          </p>
        ) : null}
        {state.status === 'ready' && state.types.length > 0 ? (
          <>
            <div className="hidden md:block" data-pay-types-table>
              <Table>
                <TableCaption className="sr-only">
                  {t('payroll.payItemTypes.caption')}
                </TableCaption>
                <TableHeader>
                  <TableRow>
                    <TableHead scope="col">
                      {t('payroll.payItemTypes.columns.name')}
                    </TableHead>
                    <TableHead scope="col">
                      {t('payroll.payItemTypes.columns.direction')}
                    </TableHead>
                    <TableHead scope="col">
                      {t('payroll.payItemTypes.columns.state')}
                    </TableHead>
                    {canManage ? (
                      <TableHead scope="col">
                        {t('payroll.payItemTypes.columns.actions')}
                      </TableHead>
                    ) : null}
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {state.types.map((type) => (
                    <TableRow
                      key={type.id}
                      data-pay-type-id={type.id}
                      className="align-top"
                    >
                      <TableCell className={`whitespace-normal ${NAME_CLASS}`}>
                        {type.name}
                      </TableCell>
                      <TableCell>
                        {t(`payroll.payItems.directions.${type.direction}`)}
                      </TableCell>
                      <TableCell>{stateWord(type)}</TableCell>
                      {canManage ? (
                        <TableCell>
                          <TypeActions
                            type={type}
                            onEdit={onEdit}
                            onToggle={startToggle}
                          />
                        </TableCell>
                      ) : null}
                    </TableRow>
                  ))}
                </TableBody>
              </Table>
            </div>
            <ul className="flex flex-col gap-3 md:hidden" data-pay-types-cards>
              {state.types.map((type) => (
                <li
                  key={type.id}
                  data-pay-type-card={type.id}
                  className="flex min-w-0 flex-col gap-2 rounded-lg border border-border p-4"
                >
                  <p className={`font-medium ${NAME_CLASS}`}>{type.name}</p>
                  <p className="text-sm">
                    {t(`payroll.payItems.directions.${type.direction}`)} ·{' '}
                    {stateWord(type)}
                  </p>
                  {canManage ? (
                    <TypeActions
                      type={type}
                      onEdit={onEdit}
                      onToggle={startToggle}
                    />
                  ) : null}
                </li>
              ))}
            </ul>
          </>
        ) : null}
        <p role="status" className="text-sm">
          {announcement}
        </p>
      </div>
      {canManage ? (
        <ConfirmDialog
          open={toggling !== null}
          title={
            toggling?.is_active
              ? t('payroll.payItemTypes.toggle.deactivateTitle')
              : t('payroll.payItemTypes.toggle.reactivateTitle')
          }
          body={
            toggling
              ? toggling.is_active
                ? t('payroll.payItemTypes.toggle.deactivateBody', {
                    name: toggling.name,
                  })
                : t('payroll.payItemTypes.toggle.reactivateBody', {
                    name: toggling.name,
                  })
              : ''
          }
          confirmLabel={
            toggling?.is_active
              ? t('payroll.payItemTypes.deactivate')
              : t('payroll.payItemTypes.reactivate')
          }
          destructive={toggling?.is_active === true}
          alert={toggleError ? <ErrorAlert error={toggleError} /> : null}
          onConfirm={confirmToggle}
          onCancel={() => {
            setToggling(null);
            setToggleError(null);
          }}
        />
      ) : null}
    </div>
  );
}
