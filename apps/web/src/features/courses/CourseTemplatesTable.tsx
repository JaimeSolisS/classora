import { useTranslation } from 'react-i18next';
import { Link } from 'react-router';
import { ErrorAlert } from '@/components/feedback/ErrorAlert';
import { LoadingState } from '@/components/feedback/LoadingState';
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from '@/components/ui/table';
import { CatalogStatusBadge } from '@/features/catalog/CatalogStatusBadge';
import { Pagination } from '@/features/catalog/Pagination';
import { TEXT_LINK_CLASS } from '@/features/students/styles';
import type { CourseTemplatePage, CourseTemplateSummary } from './api';
import { useDuration } from './useDuration';

// The course templates table (#64 G2): the name (a link to the template),
// the subjects, "sessions × minutes (total)", the module count and a text
// status badge, then the pagination. Loading, empty and error states.
// Presentational: the page passes the query state. Below `md` the table
// scrolls inside its own container (SC 1.4.10 data-table exception).

export type CourseTemplatesTableState =
  | { status: 'loading' }
  | { status: 'error'; error: unknown; onRetry: () => void }
  | { status: 'ready'; page: CourseTemplatePage };

interface CourseTemplatesTableProps {
  state: CourseTemplatesTableState;
  /** The id of the heading that names the table. */
  labelledBy: string;
  onPage: (page: number) => void;
}

function subjectsText(template: CourseTemplateSummary): string {
  return template.subjects.map((subject) => subject.name).join(', ');
}

export function CourseTemplatesTable({
  state,
  labelledBy,
  onPage,
}: CourseTemplatesTableProps) {
  const { t } = useTranslation();
  const duration = useDuration();

  if (state.status === 'loading') {
    return <LoadingState layout="table" rows={4} />;
  }
  if (state.status === 'error') {
    return <ErrorAlert error={state.error} onRetry={state.onRetry} />;
  }
  const { items, total, limit, offset } = state.page;
  if (items.length === 0) {
    return <p>{t('courses.templates.empty')}</p>;
  }
  return (
    <div className="flex flex-col gap-4">
      <Table aria-labelledby={labelledBy}>
        <TableHeader>
          <TableRow>
            <TableHead scope="col">
              {t('courses.templates.columns.name')}
            </TableHead>
            <TableHead scope="col">
              {t('courses.templates.columns.subjects')}
            </TableHead>
            <TableHead scope="col">
              {t('courses.templates.columns.duration')}
            </TableHead>
            <TableHead scope="col">
              {t('courses.templates.columns.modules')}
            </TableHead>
            <TableHead scope="col">
              {t('courses.templates.columns.status')}
            </TableHead>
          </TableRow>
        </TableHeader>
        <TableBody>
          {items.map((template) => (
            <TableRow key={template.id}>
              <TableCell className="max-w-xs whitespace-normal break-words">
                <Link
                  to={`/courses/templates/${template.id}`}
                  className={TEXT_LINK_CLASS}
                >
                  {template.name}
                </Link>
              </TableCell>
              <TableCell className="max-w-xs whitespace-normal break-words">
                {subjectsText(template)}
              </TableCell>
              <TableCell>
                {t('courses.templates.duration', {
                  count: template.session_count,
                  minutes: template.session_minutes,
                  total: duration(template.total_minutes),
                })}
              </TableCell>
              <TableCell>{template.module_count}</TableCell>
              <TableCell>
                <CatalogStatusBadge active={template.is_active} />
              </TableCell>
            </TableRow>
          ))}
        </TableBody>
      </Table>
      <Pagination
        offset={offset}
        limit={limit}
        shown={items.length}
        total={total}
        onPage={onPage}
      />
    </div>
  );
}
