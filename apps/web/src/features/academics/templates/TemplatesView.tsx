import { useEffect, useId, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { Link } from 'react-router';
import { ErrorAlert } from '@/components/feedback/ErrorAlert';
import { LoadingState } from '@/components/feedback/LoadingState';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { CatalogStatusBadge } from '@/features/catalog/CatalogStatusBadge';
import type { SubjectOption } from '@/features/courses/api';
import {
  LINK_BUTTON_CLASS,
  SELECT_CLASS,
  TEXT_LINK_CLASS,
} from '@/features/students/styles';
import {
  EVALUATION_KINDS,
  templatePath,
  type EvaluationTemplate,
  type TemplateFilters,
} from './api';
import { hasTemplateFilters, NAME_MAX } from './rules';

// "Evaluation templates" (#308 B1), presentational: the h1, "New template",
// the filters (status, kind, subject, a name search that waits 300 ms) and
// a real table in its own focusable scroll region (#311: role="region"
// named by the h1, tabIndex 0, relative) with the name (a link to the
// editor), the kind, the subject ("Any subject"), the number of criteria
// and a bordered status badge (text, never colour alone). Loading, error
// with "Try again", empty and empty-with-filters states. The API returns
// at most 200 rows, so there is no pagination.

export type TemplatesState =
  | { status: 'loading' }
  | { status: 'error'; error: unknown; onRetry: () => void }
  | { status: 'ready'; items: readonly EvaluationTemplate[] };

export interface TemplatesViewProps {
  state: TemplatesState;
  filters: TemplateFilters;
  /** A filter change (`replace` for the debounced search). */
  onFilters: (next: TemplateFilters, options?: { replace?: boolean }) => void;
  subjects: readonly SubjectOption[];
}

/** How long typing must pause before the search runs. */
export const SEARCH_DEBOUNCE_MS = 300;

const NO_FILTERS: TemplateFilters = {
  status: 'active',
  kind: null,
  subject: null,
  q: '',
};

export function TemplatesView({
  state,
  filters,
  onFilters,
  subjects,
}: TemplatesViewProps) {
  const { t } = useTranslation();
  const id = useId();
  const headingId = `${id}-title`;
  const [text, setText] = useState(filters.q);
  const [shownQ, setShownQ] = useState(filters.q);
  if (filters.q !== shownQ) {
    // Back/forward or "Clear filters" changed q: show it in the field.
    setShownQ(filters.q);
    if (text.trim() !== filters.q) {
      setText(filters.q);
    }
  }

  useEffect(() => {
    const q = text.trim().slice(0, NAME_MAX);
    if (q === filters.q) {
      return;
    }
    const timer = window.setTimeout(() => {
      onFilters({ ...filters, q }, { replace: true });
    }, SEARCH_DEBOUNCE_MS);
    return () => window.clearTimeout(timer);
    // Only the typed text and the URL's q restart the wait.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [text, filters.q]);

  const filtered = hasTemplateFilters(filters);
  const newLink = (
    <Link
      to="/academics/evaluation-templates/new"
      className={LINK_BUTTON_CLASS}
    >
      {t('academics.templates.new')}
    </Link>
  );

  let content;
  if (state.status === 'loading') {
    content = <LoadingState layout="table" rows={4} />;
  } else if (state.status === 'error') {
    content = <ErrorAlert error={state.error} onRetry={state.onRetry} />;
  } else if (state.items.length === 0) {
    content = (
      <div className="flex flex-col items-start gap-3">
        <p role="status" className="text-sm">
          {filtered
            ? t('academics.templates.emptyFiltered')
            : t('academics.templates.empty')}
        </p>
        {filtered ? (
          <Button
            type="button"
            variant="outline"
            size="sm"
            onClick={() => onFilters(NO_FILTERS)}
          >
            {t('academics.templates.clearFilters')}
          </Button>
        ) : (
          newLink
        )}
      </div>
    );
  } else {
    content = (
      <div
        role="region"
        aria-labelledby={headingId}
        tabIndex={0}
        className="relative min-w-0 overflow-x-auto rounded-sm outline-none focus-visible:ring-3 focus-visible:ring-ring"
      >
        <table
          aria-labelledby={headingId}
          className="w-full min-w-[40rem] border-collapse text-left text-sm"
        >
          <thead>
            <tr className="border-b border-border">
              <th scope="col" className="py-2 pr-3 font-medium">
                {t('academics.templates.columns.name')}
              </th>
              <th scope="col" className="py-2 pr-3 font-medium">
                {t('academics.templates.columns.kind')}
              </th>
              <th scope="col" className="py-2 pr-3 font-medium">
                {t('academics.templates.columns.subject')}
              </th>
              <th scope="col" className="py-2 pr-3 font-medium">
                {t('academics.templates.columns.criteria')}
              </th>
              <th scope="col" className="py-2 font-medium">
                {t('academics.templates.columns.status')}
              </th>
            </tr>
          </thead>
          <tbody>
            {state.items.map((template) => (
              <tr
                key={template.id}
                className="border-b border-border align-top"
              >
                <td className="max-w-xs py-2 pr-3 break-words">
                  <Link
                    to={templatePath(template.id)}
                    className={TEXT_LINK_CLASS}
                  >
                    {template.name}
                  </Link>
                </td>
                <td className="py-2 pr-3">
                  {t(`academics.kinds.${template.kind}`)}
                </td>
                <td className="max-w-xs py-2 pr-3 break-words">
                  {template.subject?.name ??
                    t('academics.templates.anySubject')}
                </td>
                <td className="py-2 pr-3">{template.criteria.length}</td>
                <td className="py-2">
                  <CatalogStatusBadge active={template.is_active} />
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    );
  }

  return (
    <div className="flex min-w-0 flex-col gap-6">
      <div className="flex flex-wrap items-center justify-between gap-4">
        <h1 id={headingId} className="text-2xl font-semibold">
          {t('academics.templates.title')}
        </h1>
        {newLink}
      </div>
      <div
        role="search"
        className="grid min-w-0 grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-4"
      >
        <div className="flex min-w-0 flex-col gap-2">
          <label htmlFor={`${id}-q`} className="text-sm font-medium">
            {t('academics.templates.filters.search')}
          </label>
          <Input
            id={`${id}-q`}
            type="search"
            autoComplete="off"
            spellCheck={false}
            maxLength={NAME_MAX}
            value={text}
            onChange={(event) => setText(event.target.value)}
          />
        </div>
        <div className="flex min-w-0 flex-col gap-2">
          <label htmlFor={`${id}-status`} className="text-sm font-medium">
            {t('academics.templates.filters.status')}
          </label>
          <select
            id={`${id}-status`}
            className={SELECT_CLASS}
            value={filters.status}
            onChange={(event) =>
              onFilters({
                ...filters,
                status: event.target.value as TemplateFilters['status'],
              })
            }
          >
            <option value="active">
              {t('academics.templates.filters.statusActive')}
            </option>
            <option value="inactive">
              {t('academics.templates.filters.statusInactive')}
            </option>
            <option value="all">
              {t('academics.templates.filters.statusAll')}
            </option>
          </select>
        </div>
        <div className="flex min-w-0 flex-col gap-2">
          <label htmlFor={`${id}-kind`} className="text-sm font-medium">
            {t('academics.templates.filters.kind')}
          </label>
          <select
            id={`${id}-kind`}
            className={SELECT_CLASS}
            value={filters.kind ?? ''}
            onChange={(event) =>
              onFilters({
                ...filters,
                kind:
                  EVALUATION_KINDS.find(
                    (kind) => kind === event.target.value,
                  ) ?? null,
              })
            }
          >
            <option value="">{t('academics.templates.filters.anyKind')}</option>
            {EVALUATION_KINDS.map((kind) => (
              <option key={kind} value={kind}>
                {t(`academics.kinds.${kind}`)}
              </option>
            ))}
          </select>
        </div>
        <div className="flex min-w-0 flex-col gap-2">
          <label htmlFor={`${id}-subject`} className="text-sm font-medium">
            {t('academics.templates.filters.subject')}
          </label>
          <select
            id={`${id}-subject`}
            className={SELECT_CLASS}
            value={filters.subject ?? ''}
            onChange={(event) =>
              onFilters({ ...filters, subject: event.target.value || null })
            }
          >
            <option value="">
              {t('academics.templates.filters.anySubject')}
            </option>
            {subjects.map((subject) => (
              <option key={subject.id} value={subject.id}>
                {t('academics.subjectOption', {
                  subject: subject.name,
                  level: subject.levelName,
                })}
              </option>
            ))}
            {filters.subject &&
            !subjects.some((subject) => subject.id === filters.subject) ? (
              <option value={filters.subject}>
                {t('academics.templates.filters.selectedSubject')}
              </option>
            ) : null}
          </select>
        </div>
      </div>
      {content}
    </div>
  );
}
