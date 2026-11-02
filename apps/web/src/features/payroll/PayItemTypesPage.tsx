import { useQuery, useQueryClient } from '@tanstack/react-query';
import { useState } from 'react';
import { useTranslation } from 'react-i18next';
import { ApiError } from '@/app/api';
import { useDocumentTitle } from '@/app/useDocumentTitle';
import { useCan } from '@/features/auth/useSession';
import {
  createPayItemType,
  fetchPayItemTypes,
  PAY_ITEM_TYPES_KEY,
  PAY_ITEMS_KEY,
  updatePayItemType,
  type PayItemType,
} from './api';
import { PayItemTypeDialog } from './PayItemTypeDialog';
import {
  payItemTypeCreateBody,
  type PayItemTypeFormValues,
} from './payItemForm';
import { PayItemTypesView, type PayItemTypesState } from './PayItemTypesView';
import { isVersionConflict } from './rules';

// /payroll/pay-item-types (#454 A), for payroll.view holders; the controls for
// payroll.manage holders. A write refreshes the types and the items, which show
// the type's name. A stale confirmation closes, says so and refreshes, so
// nobody deactivates a type they haven't seen; any other refusal stays open.

type Editing = { type: PayItemType | null };

export function PayItemTypesPage() {
  const { t } = useTranslation();
  useDocumentTitle(t('payroll.payItemTypes.title'));
  const queryClient = useQueryClient();
  const canManage = useCan('payroll.manage');
  const [editing, setEditing] = useState<Editing | null>(null);
  const [announcement, setAnnouncement] = useState('');

  const detail = useQuery({
    queryKey: PAY_ITEM_TYPES_KEY,
    queryFn: ({ signal }) => fetchPayItemTypes(signal),
  });
  const types = detail.data;
  const state: PayItemTypesState = types
    ? { status: 'ready', types }
    : detail.isError
      ? {
          status: 'error',
          error: detail.error,
          onRetry: () => void detail.refetch(),
        }
      : { status: 'loading' };

  async function refresh() {
    await Promise.all([
      queryClient.invalidateQueries({ queryKey: PAY_ITEM_TYPES_KEY }),
      queryClient.invalidateQueries({ queryKey: PAY_ITEMS_KEY }),
    ]);
  }

  async function save(values: PayItemTypeFormValues, base: PayItemType | null) {
    await (base
      ? updatePayItemType(base.id, {
          version: base.version,
          name: values.name.trim(),
        })
      : createPayItemType(payItemTypeCreateBody(values)));
    await refresh();
  }

  async function setActive(type: PayItemType, active: boolean) {
    setAnnouncement('');
    try {
      await updatePayItemType(type.id, {
        version: type.version,
        is_active: active,
      });
    } catch (caught) {
      const gone = caught instanceof ApiError && caught.status === 404;
      if (gone || isVersionConflict(caught)) {
        await refresh();
        setAnnouncement(
          gone
            ? t('payroll.payItemTypes.gone')
            : t('payroll.payItemTypes.conflictRefreshed'),
        );
        return;
      }
      throw caught;
    }
    await refresh();
    setAnnouncement(
      active
        ? t('payroll.payItemTypes.reactivated')
        : t('payroll.payItemTypes.deactivated'),
    );
  }

  // The live row, so a colleague's version reaches an open dialog; the
  // snapshot if it went missing, so the dialog never closes by itself.
  const editedType = editing?.type
    ? (types?.find((type) => type.id === editing.type?.id) ?? editing.type)
    : null;

  return (
    <>
      <PayItemTypesView
        state={state}
        canManage={canManage}
        onAdd={() => {
          setAnnouncement('');
          setEditing({ type: null });
        }}
        onEdit={(type) => {
          setAnnouncement('');
          setEditing({ type });
        }}
        onSetActive={setActive}
        announcement={announcement}
      />
      {canManage && editing ? (
        <PayItemTypeDialog
          key={editing.type?.id ?? 'new'}
          open
          type={editedType}
          onSubmit={save}
          onReload={async () => {
            await detail.refetch();
          }}
          onClose={(saved) => {
            setEditing(null);
            if (saved) {
              setAnnouncement(t('payroll.payItemTypes.saved'));
            }
          }}
        />
      ) : null}
    </>
  );
}
