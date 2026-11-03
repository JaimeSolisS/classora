import { useQuery, useQueryClient } from '@tanstack/react-query';
import { useRef, useState } from 'react';
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
// #458 C: that notice says the list reloaded only when it did; otherwise it
// says it couldn't, until a later refetch succeeds. A failed refetch keeps
// the old rows and says they may be out of date, with a reload. Closing the
// type dialog after a failed save (a lost answer may have stored it) refreshes
// the types and the items, however it closes (the #454 D2 rule).

type Notice = 'gone' | 'conflict';

type Editing = { type: PayItemType | null };

export function PayItemTypesPage() {
  const { t } = useTranslation();
  useDocumentTitle(t('payroll.payItemTypes.title'));
  const queryClient = useQueryClient();
  const canManage = useCan('payroll.manage');
  const [editing, setEditing] = useState<Editing | null>(null);
  const [announcement, setAnnouncement] = useState('');
  // A gone or changed type whose reload failed: said until a refetch lands.
  const [notReloaded, setNotReloaded] = useState<Notice | null>(null);
  const saveFailed = useRef(false);

  const detail = useQuery({
    queryKey: PAY_ITEM_TYPES_KEY,
    queryFn: ({ signal }) => fetchPayItemTypes(signal),
  });
  const types = detail.data;
  const reloadedText = (notice: Notice) =>
    notice === 'gone'
      ? t('payroll.payItemTypes.gone')
      : t('payroll.payItemTypes.conflictRefreshed');
  // Adjusting state while rendering: once a refetch lands, the notice says
  // the list reloaded (#454 D4).
  if (notReloaded && detail.isSuccess && !detail.isFetching) {
    setNotReloaded(null);
    setAnnouncement(reloadedText(notReloaded));
  }
  const state: PayItemTypesState = types
    ? {
        status: 'ready',
        types,
        refreshError: detail.isError ? detail.error : null,
        onRefresh: () => void detail.refetch(),
      }
    : detail.isError
      ? {
          status: 'error',
          error: detail.error,
          onRetry: () => void detail.refetch(),
        }
      : { status: 'loading' };

  /** Whether the types reloaded: invalidating never rejects. */
  async function refresh(): Promise<boolean> {
    await Promise.all([
      queryClient.invalidateQueries({ queryKey: PAY_ITEM_TYPES_KEY }),
      queryClient.invalidateQueries({ queryKey: PAY_ITEMS_KEY }),
    ]);
    return queryClient.getQueryState(PAY_ITEM_TYPES_KEY)?.status !== 'error';
  }

  function announce(text: string) {
    setNotReloaded(null);
    setAnnouncement(text);
  }

  async function save(values: PayItemTypeFormValues, base: PayItemType | null) {
    try {
      await (base
        ? updatePayItemType(base.id, {
            version: base.version,
            name: values.name.trim(),
          })
        : createPayItemType(payItemTypeCreateBody(values)));
    } catch (caught) {
      saveFailed.current = true;
      throw caught;
    }
    saveFailed.current = false;
    await refresh();
  }

  async function setActive(type: PayItemType, active: boolean) {
    announce('');
    try {
      await updatePayItemType(type.id, {
        version: type.version,
        is_active: active,
      });
    } catch (caught) {
      const gone = caught instanceof ApiError && caught.status === 404;
      if (gone || isVersionConflict(caught)) {
        const notice: Notice = gone ? 'gone' : 'conflict';
        if (await refresh()) {
          announce(reloadedText(notice));
        } else {
          setAnnouncement(
            notice === 'gone'
              ? t('payroll.payItemTypes.goneNotReloaded')
              : t('payroll.payItemTypes.conflictNotReloaded'),
          );
          setNotReloaded(notice);
        }
        return;
      }
      throw caught;
    }
    await refresh();
    announce(
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
          announce('');
          setEditing({ type: null });
        }}
        onEdit={(type) => {
          announce('');
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
              announce(t('payroll.payItemTypes.saved'));
            }
            if (saveFailed.current) {
              saveFailed.current = false;
              void refresh();
            }
          }}
        />
      ) : null}
    </>
  );
}
