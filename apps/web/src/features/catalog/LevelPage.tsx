import {
  keepPreviousData,
  useQuery,
  useQueryClient,
} from '@tanstack/react-query';
import { useId, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { Link, useParams, useSearchParams } from 'react-router';
import { ApiError } from '@/app/api';
import { useDocumentTitle } from '@/app/useDocumentTitle';
import { ErrorAlert } from '@/components/feedback/ErrorAlert';
import { LoadingState } from '@/components/feedback/LoadingState';
import { Button } from '@/components/ui/button';
import {
  createSubject,
  fetchLevel,
  fetchSubjects,
  levelKey,
  LEVELS_QUERY_KEY,
  SUBJECTS_QUERY_KEY,
  subjectsListKey,
  updateSubject,
  type EducationLevel,
  type Subject,
  type SubjectUpdate,
} from './api';
import { CatalogItemDialog } from './CatalogItemDialog';
import { CatalogStatusBadge } from './CatalogStatusBadge';
import { CATALOG_LINK_CLASS } from './LevelsTable';
import { pageParam, subjectUpdateBody } from './rules';
import { SubjectsTable, type SubjectsTableState } from './SubjectsTable';

// /catalog/education-levels/:levelId (#32 I): the level's name and status,
// and its subjects (active or not) with "Add subject" and "Edit". After a
// change the subject queries and the level queries (whose counts change) are
// invalidated. An inactive level shows a note and disables "Add subject";
// the API still answers 409 education_level_inactive.

export function LevelPage() {
  const { t } = useTranslation();
  const { levelId = '' } = useParams();
  const queryClient = useQueryClient();
  const [searchParams, setSearchParams] = useSearchParams();
  const page = pageParam(searchParams);
  const level = useQuery({
    queryKey: levelKey(levelId),
    queryFn: ({ signal }) => fetchLevel(levelId, signal),
  });
  const subjects = useQuery({
    queryKey: subjectsListKey(levelId, page),
    queryFn: ({ signal }) => fetchSubjects(levelId, page, signal),
    placeholderData: keepPreviousData,
    enabled: level.isSuccess,
  });
  useDocumentTitle(level.data?.name ?? t('catalog.title'));

  async function refresh() {
    await Promise.all([
      queryClient.invalidateQueries({ queryKey: SUBJECTS_QUERY_KEY }),
      queryClient.invalidateQueries({ queryKey: LEVELS_QUERY_KEY }),
    ]);
  }

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

  if (level.isError) {
    const notFound =
      level.error instanceof ApiError && level.error.status === 404;
    return (
      <div className="flex flex-col gap-4">
        <h1 className="text-2xl font-semibold">
          {notFound ? t('catalog.levels.notFound') : t('catalog.title')}
        </h1>
        {notFound ? null : (
          <ErrorAlert
            error={level.error}
            onRetry={() => void level.refetch()}
          />
        )}
        <BackLink />
      </div>
    );
  }
  if (!level.data) {
    return <LoadingState />;
  }

  const subjectsState: SubjectsTableState = subjects.data
    ? { status: 'ready', page: subjects.data }
    : subjects.isError
      ? {
          status: 'error',
          error: subjects.error,
          onRetry: () => void subjects.refetch(),
        }
      : { status: 'loading' };

  return (
    <LevelPageView
      level={level.data}
      subjects={subjectsState}
      onPage={goToPage}
      onCreateSubject={async (name) => {
        await createSubject(levelId, name);
        await refresh();
      }}
      onUpdateSubject={async (subject, body) => {
        await updateSubject(subject.id, body);
        await refresh();
      }}
      onReload={async () => {
        await Promise.all([level.refetch(), subjects.refetch()]);
      }}
    />
  );
}

function BackLink() {
  const { t } = useTranslation();
  return (
    <p>
      <Link to="/catalog/education-levels" className={CATALOG_LINK_CLASS}>
        {t('catalog.levels.back')}
      </Link>
    </p>
  );
}

interface LevelPageViewProps {
  level: EducationLevel;
  subjects: SubjectsTableState;
  onPage: (page: number) => void;
  /** Adds a subject; rejects with the error to show in the dialog. */
  onCreateSubject: (name: string) => Promise<void>;
  /** Sends a subject change (`version` plus the changed fields). */
  onUpdateSubject: (subject: Subject, body: SubjectUpdate) => Promise<void>;
  /** Refetches the level and its subjects (after a version conflict). */
  onReload: () => Promise<void>;
}

type DialogState = { mode: 'create' } | { mode: 'edit'; subject: Subject };

/** The level page's content, without any query (stories use it). */
export function LevelPageView({
  level,
  subjects,
  onPage,
  onCreateSubject,
  onUpdateSubject,
  onReload,
}: LevelPageViewProps) {
  const { t } = useTranslation();
  const id = useId();
  const ids = { subjects: `${id}-subjects`, note: `${id}-note` };
  const [dialog, setDialog] = useState<DialogState | null>(null);
  const [saved, setSaved] = useState(false);

  // The edited subject as loaded now (a Reload brings a new version).
  const editing =
    dialog?.mode === 'edit'
      ? ((subjects.status === 'ready'
          ? subjects.page.items.find((item) => item.id === dialog.subject.id)
          : undefined) ?? dialog.subject)
      : null;

  function open(next: DialogState) {
    setSaved(false);
    setDialog(next);
  }

  return (
    <div className="flex flex-col gap-8">
      <div className="flex flex-col gap-3">
        <BackLink />
        <h1 className="text-2xl font-semibold break-words">{level.name}</h1>
        <p className="flex flex-wrap items-center gap-2 text-sm">
          <span className="font-medium">{t('catalog.levels.status')}</span>
          <CatalogStatusBadge active={level.is_active} />
        </p>
        {level.is_active ? null : (
          <p id={ids.note} className="text-sm">
            {t('catalog.levels.inactiveNote')}
          </p>
        )}
      </div>
      <section aria-labelledby={ids.subjects} className="flex flex-col gap-4">
        <div className="flex flex-wrap items-center justify-between gap-4">
          <h2 id={ids.subjects} className="text-lg font-semibold">
            {t('catalog.subjects.title')}
          </h2>
          <Button
            type="button"
            disabled={!level.is_active}
            aria-describedby={level.is_active ? undefined : ids.note}
            onClick={() => open({ mode: 'create' })}
          >
            {t('catalog.subjects.add')}
          </Button>
        </div>
        <p role="status" className="text-sm">
          {saved ? t('catalog.saved') : null}
        </p>
        <SubjectsTable
          state={subjects}
          labelledBy={ids.subjects}
          onPage={onPage}
          onEdit={(subject) => open({ mode: 'edit', subject })}
        />
      </section>
      <CatalogItemDialog
        open={dialog !== null}
        title={editing ? t('catalog.subjects.edit') : t('catalog.subjects.add')}
        item={
          editing
            ? {
                name: editing.name,
                isActive: editing.is_active,
                version: editing.version,
              }
            : null
        }
        onSubmit={async (values) => {
          if (!editing) {
            await onCreateSubject(values.name);
            return;
          }
          const body = subjectUpdateBody(editing, values);
          if (body) {
            await onUpdateSubject(editing, body);
          }
        }}
        onSetActive={async (active) => {
          if (editing) {
            await onUpdateSubject(editing, {
              version: editing.version,
              is_active: active,
            });
          }
        }}
        onReload={onReload}
        onClose={(didSave) => {
          setDialog(null);
          setSaved(didSave);
        }}
      />
    </div>
  );
}
