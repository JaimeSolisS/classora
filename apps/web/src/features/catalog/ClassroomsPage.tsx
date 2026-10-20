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
  CLASSROOMS_QUERY_KEY,
  classroomsListKey,
  createClassroom,
  fetchClassrooms,
  updateClassroom,
  type Classroom,
} from './api';
import { CatalogTabs } from './CatalogTabs';
import { ClassroomDialog } from './ClassroomDialog';
import {
  classroomCreateBody,
  classroomUpdateBody,
  type ClassroomFormValues,
} from './classroomRules';
import { ClassroomsTable, type ClassroomsTableState } from './ClassroomsTable';
import { pageParam } from './rules';

// /catalog/classrooms (#39 D1-D4): every classroom, active or not, for
// holders of catalog.manage, by name. "Add classroom" and each row's
// "Edit" open the classroom dialog; after a change the classroom queries
// are invalidated and a polite status says "Saved.". The page lives in the
// URL (?page=).

type DialogState = { mode: 'create' } | { mode: 'edit'; classroom: Classroom };

export function ClassroomsPage() {
  const { t } = useTranslation();
  useDocumentTitle(t('catalog.classrooms.title'));
  const titleId = useId();
  const queryClient = useQueryClient();
  const [searchParams, setSearchParams] = useSearchParams();
  const page = pageParam(searchParams);
  const [dialog, setDialog] = useState<DialogState | null>(null);
  const [saved, setSaved] = useState(false);

  const list = useQuery({
    queryKey: classroomsListKey(page),
    queryFn: ({ signal }) => fetchClassrooms(page, signal),
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

  // The edited classroom as loaded now (a Reload brings a new version).
  const editing =
    dialog?.mode === 'edit'
      ? (list.data?.items.find((item) => item.id === dialog.classroom.id) ??
        dialog.classroom)
      : null;

  async function refresh() {
    await queryClient.invalidateQueries({ queryKey: CLASSROOMS_QUERY_KEY });
  }

  async function submit(values: ClassroomFormValues) {
    if (!editing) {
      await createClassroom(classroomCreateBody(values));
    } else {
      const body = classroomUpdateBody(editing, values);
      if (body) {
        await updateClassroom(editing.id, body);
      }
    }
    await refresh();
  }

  async function setActive(active: boolean) {
    if (!editing) {
      return;
    }
    await updateClassroom(editing.id, {
      version: editing.version,
      is_active: active,
    });
    await refresh();
  }

  function open(next: DialogState) {
    setSaved(false);
    setDialog(next);
  }

  const tableState: ClassroomsTableState = list.data
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
      <CatalogTabs />
      <div className="flex flex-wrap items-center justify-between gap-4">
        <h1 id={titleId} className="text-2xl font-semibold">
          {t('catalog.classrooms.title')}
        </h1>
        <Button type="button" onClick={() => open({ mode: 'create' })}>
          {t('catalog.classrooms.add')}
        </Button>
      </div>
      <p role="status" className="text-sm">
        {saved ? t('catalog.saved') : null}
      </p>
      <ClassroomsTable
        state={tableState}
        labelledBy={titleId}
        onPage={goToPage}
        onEdit={(classroom) => open({ mode: 'edit', classroom })}
      />
      <ClassroomDialog
        open={dialog !== null}
        classroom={editing}
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
