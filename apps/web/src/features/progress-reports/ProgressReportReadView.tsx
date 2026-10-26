import type { ReactNode, Ref } from 'react';
import { useTranslation } from 'react-i18next';
import { useFormatters } from '@/lib/formatters';
import type { ApprovedReport } from './api';
import { NarrativeView } from './Narrative';
import { ProgressReportFacts } from './ProgressReportFacts';
import { usePeriodText } from './text';

// An approved report (#315 F1; R11's closed shape), shared by the staff and
// tutor detail and the family detail: the h1, the kind and period, the
// subject, "Written by", "Approved on", the narrative and the facts as
// approved. Never a status, a reason, a reviewer or a version (the shape
// has none).

export function ProgressReportReadView({
  report,
  actions,
  headingRef,
}: {
  report: ApprovedReport;
  /** Links under the header ("Open review page"). */
  actions?: ReactNode;
  headingRef?: Ref<HTMLHeadingElement>;
}) {
  const { t } = useTranslation();
  const { formatInstant } = useFormatters();
  const periods = usePeriodText();
  return (
    <article className="flex w-full max-w-5xl min-w-0 flex-col gap-6">
      <header className="flex min-w-0 flex-col gap-2">
        <h1
          ref={headingRef}
          tabIndex={-1}
          className="text-2xl font-semibold outline-none"
        >
          {t('progressReports.reportTitle')}
        </h1>
        <p className="text-sm font-medium break-words">
          {periods.line(report)}
        </p>
        <p className="text-sm break-words">
          {report.subject?.name ?? t('progressReports.allSubjects')}
        </p>
        <p className="text-sm break-words text-muted-foreground">
          {t('progressReports.writtenBy', {
            name: report.author.display_name,
          })}
        </p>
        <p className="text-sm text-muted-foreground">
          {t('progressReports.approvedOn', {
            date: formatInstant(report.approved_at, 'date'),
          })}
        </p>
        {actions}
      </header>
      <div className="grid min-w-0 grid-cols-1 gap-6 lg:grid-cols-[minmax(0,1fr)_minmax(0,24rem)]">
        <NarrativeView report={report} />
        <ProgressReportFacts
          facts={report.facts}
          heading={t('progressReports.facts.asApproved')}
        />
      </div>
    </article>
  );
}
