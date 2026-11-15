import { keepPreviousData, useQuery } from '@tanstack/react-query';
import { useTranslation } from 'react-i18next';
import { useSearchParams } from 'react-router';
import { useDocumentTitle } from '@/app/useDocumentTitle';
import {
  fetchSubjectOptions,
  SUBJECT_OPTIONS_KEY,
} from '@/features/courses/api';
import { fetchEvaluationTemplates, templatesKey } from './api';
import { parseTemplateFilters, templateFilterParams } from './rules';
import { TemplatesView, type TemplatesState } from './TemplatesView';

// /academics/evaluation-templates (#308 B1), for evaluation.manage holders
// (the route guard): the filters live in the URL (status, kind, subject,
// q), invalid values are ignored. Code-split by the router.

export function TemplatesPage() {
  const { t } = useTranslation();
  useDocumentTitle(t('academics.templates.title'));
  const [searchParams, setSearchParams] = useSearchParams();
  const filters = parseTemplateFilters(searchParams);
  const list = useQuery({
    queryKey: templatesKey(filters),
    queryFn: ({ signal }) => fetchEvaluationTemplates(filters, signal),
    placeholderData: keepPreviousData,
  });
  const subjects = useQuery({
    queryKey: SUBJECT_OPTIONS_KEY,
    queryFn: ({ signal }) => fetchSubjectOptions(signal),
  });

  const state: TemplatesState = list.data
    ? { status: 'ready', items: list.data }
    : list.isError
      ? {
          status: 'error',
          error: list.error,
          onRetry: () => void list.refetch(),
        }
      : { status: 'loading' };

  return (
    <TemplatesView
      state={state}
      filters={filters}
      subjects={subjects.data ?? []}
      onFilters={(next, options) =>
        setSearchParams(templateFilterParams(next), options)
      }
    />
  );
}

export default TemplatesPage;
