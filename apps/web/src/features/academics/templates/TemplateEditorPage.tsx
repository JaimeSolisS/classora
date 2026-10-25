import { useQuery, useQueryClient } from '@tanstack/react-query';
import { useState } from 'react';
import { useTranslation } from 'react-i18next';
import { Link, useLocation, useNavigate, useParams } from 'react-router';
import { ApiError } from '@/app/api';
import { useDocumentTitle } from '@/app/useDocumentTitle';
import { ErrorAlert } from '@/components/feedback/ErrorAlert';
import { LoadingState } from '@/components/feedback/LoadingState';
import {
  fetchSubjectOptions,
  SUBJECT_OPTIONS_KEY,
} from '@/features/courses/api';
import { NotFoundPage } from '@/pages/NotFoundPage';
import { SMALL_LINK_CLASS } from '../styles';
import {
  createEvaluationTemplate,
  fetchEvaluationTemplate,
  replaceEvaluationTemplate,
  templateKey,
  templatePath,
  TEMPLATES_KEY,
} from './api';
import { createBody, replaceBody } from './rules';
import { TemplateEditor } from './TemplateEditor';

// /academics/evaluation-templates/new and /:templateId (#308 B2), for
// evaluation.manage holders (the route guard). A create goes to the new
// template's editor (replacing the history entry), which says "Template
// saved."; an edit stays. A 404 shows the not-found page. Code-split by the
// router.

function BackLink() {
  const { t } = useTranslation();
  return (
    <Link
      to="/academics/evaluation-templates"
      className={`${SMALL_LINK_CLASS} self-start`}
    >
      {t('academics.templates.back')}
    </Link>
  );
}

function useSubjects() {
  const subjects = useQuery({
    queryKey: SUBJECT_OPTIONS_KEY,
    queryFn: ({ signal }) => fetchSubjectOptions(signal),
  });
  return subjects.data ?? [];
}

export function NewTemplatePage() {
  const { t } = useTranslation();
  useDocumentTitle(t('academics.templates.newTitle'));
  const navigate = useNavigate();
  const client = useQueryClient();
  const subjects = useSubjects();
  return (
    <div className="flex min-w-0 flex-col gap-6">
      <BackLink />
      <h1 className="text-2xl font-semibold">
        {t('academics.templates.newTitle')}
      </h1>
      <TemplateEditor
        template={null}
        subjects={subjects}
        onReload={() => Promise.resolve()}
        onSave={async (values) => {
          const created = await createEvaluationTemplate(createBody(values));
          client.setQueryData(templateKey(created.id), created);
          void client.invalidateQueries({
            queryKey: [...TEMPLATES_KEY, 'list'],
          });
          void navigate(templatePath(created.id), {
            replace: true,
            state: { saved: true },
          });
        }}
      />
    </div>
  );
}

export function TemplateEditPage() {
  const { t } = useTranslation();
  const { templateId = '' } = useParams();
  const location = useLocation();
  const client = useQueryClient();
  const subjects = useSubjects();
  const [missing, setMissing] = useState(false);
  const key = templateKey(templateId);
  const detail = useQuery({
    queryKey: key,
    queryFn: ({ signal }) => fetchEvaluationTemplate(templateId, signal),
  });
  useDocumentTitle(detail.data?.name ?? t('academics.templates.title'));
  const justCreated =
    (location.state as { saved?: boolean } | null)?.saved === true;

  if (
    missing ||
    (detail.error instanceof ApiError && detail.error.status === 404)
  ) {
    return <NotFoundPage />;
  }

  return (
    <div className="flex min-w-0 flex-col gap-6">
      <BackLink />
      <h1 className="text-2xl font-semibold break-words">
        {detail.data?.name ?? t('academics.templates.title')}
      </h1>
      {detail.isError ? (
        <ErrorAlert
          error={detail.error}
          onRetry={() => void detail.refetch()}
        />
      ) : !detail.data ? (
        <LoadingState rows={6} />
      ) : (
        <TemplateEditor
          template={detail.data}
          subjects={subjects}
          initiallySaved={justCreated}
          onReload={async () => {
            await detail.refetch();
          }}
          onSave={async (values) => {
            try {
              const saved = await replaceEvaluationTemplate(
                templateId,
                replaceBody(values, detail.data),
              );
              client.setQueryData(key, saved);
              void client.invalidateQueries({
                queryKey: [...TEMPLATES_KEY, 'list'],
              });
            } catch (caught) {
              if (caught instanceof ApiError && caught.status === 404) {
                setMissing(true);
                return;
              }
              throw caught;
            }
          }}
        />
      )}
    </div>
  );
}
