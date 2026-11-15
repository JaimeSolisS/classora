import { useQuery, useQueryClient } from '@tanstack/react-query';
import { useRef, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { useNavigate, useSearchParams } from 'react-router';
import { ApiError } from '@/app/api';
import { useDocumentTitle } from '@/app/useDocumentTitle';
import { ForbiddenPage } from '@/features/auth/ForbiddenPage';
import { useCenterTimeZone } from '@/features/auth/useCenterTimeZone';
import { useSession } from '@/features/auth/useSession';
import { todayInZone } from '@/lib/time';
import {
  createExpenseTemplate,
  fetchExpenseTemplates,
  TEMPLATES_KEY,
  templatesListKey,
} from '../api';
import type { SaveResult } from '../useRowSave';
import { TemplateDialog } from './TemplateDialog';
import { usePickers } from './usePickers';
import {
  TemplatesView,
  type TemplateFilterChange,
  type TemplatesState,
} from './TemplatesView';
import {
  isTemplateFiltered,
  templateCapabilities,
  templateCreateBody,
  templateListParams,
  templateListSearch,
  templatePath,
  type TemplateFormValues,
} from './rules';

// /expenses/templates (M7-13b B, D1), for expense.view holders. The filters
// live in the URL. "Nueva plantilla" (expense.manage) opens the dialog; a 201
// opens the new template. The #458 config-page rules (E2 (a)): closing the
// dialog after a failed save (a lost answer may have stored it) refetches
// the list however it closes, and "Nueva plantilla" waits for that refetch;
// a failed refetch keeps the rows with the out-of-date alert and never says
// it reloaded (B3). A 403 is the forbidden page.

type Block = { kind: 'refreshing' } | { kind: 'stale'; error: unknown };

const L = 'finance.templates';

export function TemplatesPage() {
  const { t } = useTranslation();
  useDocumentTitle(t(`${L}.title`));
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const session = useSession();
  const user = session.status === 'signedIn' ? session.user : null;
  const can = templateCapabilities(user);
  const timeZone = useCenterTimeZone();
  const [searchParams, setSearchParams] = useSearchParams();
  const params = templateListParams(searchParams);
  const [creating, setCreating] = useState(false);
  const [block, setBlock] = useState<Block | null>(null);
  const saveFailed = useRef(false);
  const createdId = useRef<string | null>(null);
  const pickers = usePickers();

  const listKey = templatesListKey(params);
  const list = useQuery({
    queryKey: listKey,
    queryFn: ({ signal }) => fetchExpenseTemplates(params, signal),
    meta: { expectedStatuses: [403] },
  });

  /** Refetches the list; whether it reloaded. Holds "Nueva plantilla". */
  async function reload(): Promise<boolean> {
    setBlock({ kind: 'refreshing' });
    await queryClient.invalidateQueries({
      queryKey: [...TEMPLATES_KEY, 'list'],
    });
    const state = queryClient.getQueryState(listKey);
    const failed = state?.status === 'error';
    setBlock(failed ? { kind: 'stale', error: state.error } : null);
    return !failed;
  }

  function onFilter(change: TemplateFilterChange) {
    setSearchParams(
      new URLSearchParams(templateListSearch({ ...params, ...change })),
    );
  }

  async function save(values: TemplateFormValues): Promise<SaveResult> {
    try {
      const created = await createExpenseTemplate(templateCreateBody(values));
      createdId.current = created.id;
    } catch (caught) {
      saveFailed.current = true;
      await pickers.reloadIfNeeded(caught);
      throw caught;
    }
    saveFailed.current = false;
    return 'saved';
  }

  function closed(saved: boolean) {
    setCreating(false);
    const id = createdId.current;
    createdId.current = null;
    if (saved && id) {
      saveFailed.current = false;
      void queryClient.invalidateQueries({ queryKey: TEMPLATES_KEY });
      void navigate(templatePath(id), { state: { created: true } });
      return;
    }
    if (saveFailed.current) {
      saveFailed.current = false;
      void reload();
    }
  }

  if (list.error instanceof ApiError && list.error.status === 403) {
    return <ForbiddenPage />;
  }

  const state: TemplatesState = list.data
    ? {
        status: 'ready',
        templates: list.data,
        refreshError: list.isError
          ? list.error
          : block?.kind === 'stale'
            ? block.error
            : null,
        onRefresh: () => void reload(),
      }
    : list.isError
      ? {
          status: 'error',
          error: list.error,
          onRetry: () => void list.refetch(),
        }
      : { status: 'loading' };

  return (
    <>
      <TemplatesView
        state={state}
        params={params}
        onFilter={onFilter}
        categories={pickers.categories.data ?? null}
        vendors={pickers.vendors.data ?? null}
        filtered={isTemplateFiltered(params)}
        canAdd={can.manage}
        addBlocked={block !== null || creating}
        onAdd={() => setCreating(true)}
      />
      {can.manage && creating ? (
        <TemplateDialog
          open
          template={null}
          today={todayInZone(timeZone)}
          categories={pickers.categoryPicker}
          vendors={pickers.vendorPicker}
          onSubmit={save}
          onReload={reload}
          onClose={closed}
        />
      ) : null}
    </>
  );
}
