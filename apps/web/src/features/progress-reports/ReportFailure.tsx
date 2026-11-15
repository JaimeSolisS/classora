import type { Ref } from 'react';
import { useTranslation } from 'react-i18next';
import { ErrorAlert } from '@/components/feedback/ErrorAlert';
import type { ReportRefusal } from './rules';

// The failure region of the report page (#315 C9, E4), focusable so focus
// moves to it after a refused write:
// - version_conflict: "This report changed elsewhere." (editing) or "This
//   report changed since you opened it. Reload to review the latest
//   version." (reviewing), with Reload;
// - a changed state or a refused role: its message with Reload;
// - self-review: "You wrote this report, so another reviewer must review
//   it.";
// - the author no longer teaches the student: the E4 text with Reload;
// - anything else: the error's own message.

export interface ReportFailureState {
  refusal: ReportRefusal;
  error: unknown;
}

export function ReportFailure({
  failure,
  mode,
  author,
  student,
  onReload,
  ref,
}: {
  failure: ReportFailureState;
  mode: 'edit' | 'review' | 'read';
  author: string;
  student: string;
  onReload: () => void;
  ref?: Ref<HTMLDivElement>;
}) {
  const { t } = useTranslation();
  const { refusal, error } = failure;
  const reload = { retryLabel: t('academics.reload'), onRetry: onReload };
  let content;
  if (refusal.kind === 'conflict') {
    content = (
      <ErrorAlert
        error={error}
        message={
          mode === 'edit'
            ? t('progressReports.conflict.edit')
            : t('progressReports.conflict.review')
        }
        {...reload}
      />
    );
  } else if (refusal.kind === 'state' || refusal.kind === 'forbidden') {
    content = <ErrorAlert error={error} {...reload} />;
  } else if (refusal.kind === 'selfReview') {
    content = (
      <ErrorAlert
        error={error}
        message={t('progressReports.review.selfReview')}
      />
    );
  } else if (refusal.kind === 'authorNotCurrent') {
    content = (
      <ErrorAlert
        error={error}
        message={t('progressReports.review.authorNotCurrent', {
          author,
          student,
        })}
        {...reload}
      />
    );
  } else {
    content = <ErrorAlert error={error} />;
  }
  return (
    <div ref={ref} tabIndex={-1} className="min-w-0 outline-none">
      {content}
    </div>
  );
}
