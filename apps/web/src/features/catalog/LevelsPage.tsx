import {
  keepPreviousData,
  useQuery,
  useQueryClient,
} from '@tanstack/react-query';
import { useId, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { useSearchParams } from 'react-router';
import { useDocumentTitle } from '@/app/useDocumentTitle';
import { Button } from '@/components/ui/button';
import {
  createLevel,
  fetchLevels,
  LEVELS_QUERY_KEY,
  levelsListKey,
  updateLevel,
  type EducationLevel,
} from './api';
import { CatalogItemDialog } from './CatalogItemDialog';
import { LevelsTable, type LevelsTableState } from './LevelsTable';
import {
  levelCreateBody,
  levelUpdateBody,
  pageParam,
  type CatalogFormValues,
} from './rules';

// /catalog/education-levels (#32 H): every level, active or not, for holders
// of catalog.manage. "Add level" and each row's "Edit" open the level dialog;
// after a change the level queries are invalidated (and refetched before the
// dialog closes) and a polite status says "Saved.". The page lives in the
// URL (?page=).

type DialogState = { mode: 'create' } | { mode: 'edit'; level: EducationLevel };

export function LevelsPage() {
  const { t } = useTranslation();
  useDocumentTitle(t('catalog.title'));
  const titleId = useId();
  const queryClient = useQueryClient();
  const [searchParams, setSearchParams] = useSearchParams();
  const page = pageParam(searchParams);
  const [dialog, setDialog] = useState<DialogState | null>(null);
  const [saved, setSaved] = useState(false);

  const list = useQuery({
    queryKey: levelsListKey(page),
    queryFn: ({ signal }) => fetchLevels(page, signal),
    placeholderData: keepPreviousData,
  });

  function goToPage(next: number) {
    setSearchParams((previous) => {
      const params = new URLSearchParams(previous);
      if (next > 1) {
        params.set('page', String(next));
      } else {
        params.delete('page');
      }
      return params;
    });
  }

  // The edited level as loaded now (a Reload brings a new version).
  const editing =
    dialog?.mode === 'edit'
      ? (list.data?.items.find((item) => item.id === dialog.level.id) ??
        dialog.level)
      : null;

  async function refresh() {
    await queryClient.invalidateQueries({ queryKey: LEVELS_QUERY_KEY });
  }

  async function submit(values: CatalogFormValues) {
    if (!editing) {
      await createLevel(levelCreateBody(values));
    } else {
      const body = levelUpdateBody(editing, values);
      if (body) {
        await updateLevel(editing.id, body);
      }
    }
    await refresh();
  }

  async function setActive(active: boolean) {
    if (!editing) {
      return;
    }
    await updateLevel(editing.id, {
      version: editing.version,
      is_active: active,
    });
    await refresh();
  }

  function open(next: DialogState) {
    setSaved(false);
    setDialog(next);
  }

  const tableState: LevelsTableState = list.data
    ? { status: 'ready', page: list.data }
    : list.isError
      ? {
          status: 'error',
          error: list.error,
          onRetry: () => void list.refetch(),
        }
      : { status: 'loading' };

  return (
    <div className="flex flex-col gap-8">
      <div className="flex flex-wrap items-center justify-between gap-4">
        <h1 id={titleId} className="text-2xl font-semibold">
          {t('catalog.title')}
        </h1>
        <Button type="button" onClick={() => open({ mode: 'create' })}>
          {t('catalog.levels.add')}
        </Button>
      </div>
      <p role="status" className="text-sm">
        {saved ? t('catalog.saved') : null}
      </p>
      <LevelsTable
        state={tableState}
        labelledBy={titleId}
        onPage={goToPage}
        onEdit={(level) => open({ mode: 'edit', level })}
      />
      <CatalogItemDialog
        open={dialog !== null}
        title={editing ? t('catalog.levels.edit') : t('catalog.levels.add')}
        withPosition
        item={
          editing
            ? {
                name: editing.name,
                sortOrder: editing.sort_order,
                isActive: editing.is_active,
                version: editing.version,
              }
            : null
        }
        onSubmit={submit}
        onSetActive={setActive}
        onReload={async () => {
          await list.refetch();
        }}
        onClose={(didSave) => {
          setDialog(null);
          setSaved(didSave);
        }}
      />
    </div>
  );
}
