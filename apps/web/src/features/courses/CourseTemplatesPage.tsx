import { keepPreviousData, useQuery } from '@tanstack/react-query';
import { useId } from 'react';
import { useTranslation } from 'react-i18next';
import { Link, useSearchParams } from 'react-router';
import { useDocumentTitle } from '@/app/useDocumentTitle';
import { useCan } from '@/features/auth/useSession';
import { pageParam } from '@/features/catalog/rules';
import { LINK_BUTTON_CLASS, SELECT_CLASS } from '@/features/students/styles';
import {
  fetchTemplates,
  templatesListKey,
  type CourseTemplateStatus,
} from './api';
import {
  CourseTemplatesTable,
  type CourseTemplatesTableState,
} from './CourseTemplatesTable';

// /courses (#64 G2): the course templates for holders of course.view, with
// a status filter (active by default) and pagination, both in the URL
// (?status=, ?page=). Holders of course.manage get "New template".

const STATUSES: readonly CourseTemplateStatus[] = ['active', 'inactive', 'all'];

function statusParam(params: URLSearchParams): CourseTemplateStatus {
  const value = params.get('status');
  return STATUSES.find((status) => status === value) ?? 'active';
}

export function CourseTemplatesPage() {
  const { t } = useTranslation();
  useDocumentTitle(t('courses.templates.title'));
  const id = useId();
  const ids = { title: `${id}-title`, status: `${id}-status` };
  const canManage = useCan('course.manage');
  const [searchParams, setSearchParams] = useSearchParams();
  const status = statusParam(searchParams);
  const page = pageParam(searchParams);

  const list = useQuery({
    queryKey: templatesListKey(status, page),
    queryFn: ({ signal }) => fetchTemplates(status, page, signal),
    placeholderData: keepPreviousData,
  });

  function setStatus(next: string) {
    setSearchParams((previous) => {
      const params = new URLSearchParams(previous);
      if (next === 'active') {
        params.delete('status');
      } else {
        params.set('status', next);
      }
      params.delete('page');
      return params;
    });
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

  const tableState: CourseTemplatesTableState = list.data
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
        <h1 id={ids.title} className="text-2xl font-semibold">
          {t('courses.templates.title')}
        </h1>
        {canManage ? (
          <Link to="/courses/templates/new" className={LINK_BUTTON_CLASS}>
            {t('courses.templates.new')}
          </Link>
        ) : null}
      </div>
      <div role="search" className="flex max-w-xs flex-col gap-2">
        <label htmlFor={ids.status} className="text-sm font-medium">
          {t('courses.templates.statusFilter')}
        </label>
        <select
          id={ids.status}
          className={SELECT_CLASS}
          value={status}
          onChange={(event) => setStatus(event.target.value)}
        >
          {STATUSES.map((value) => (
            <option key={value} value={value}>
              {t(`courses.templates.statusOptions.${value}`)}
            </option>
          ))}
        </select>
      </div>
      <CourseTemplatesTable
        state={tableState}
        labelledBy={ids.title}
        onPage={goToPage}
      />
    </div>
  );
}
