import {
  useEffect,
  useRef,
  useState,
  type ReactNode,
  type RefObject,
} from 'react';
import { useTranslation } from 'react-i18next';
import { useSearchParams } from 'react-router';
import { ErrorAlert } from '@/components/feedback/ErrorAlert';
import { LoadingState } from '@/components/feedback/LoadingState';
import { useCenterTimeZone } from '@/features/auth/useCenterTimeZone';
import { ConsentRequired } from '@/features/my-schedule/ConsentRequired';
import { todayInZone } from '@/lib/time';
import { FAMILY_EXPECTED_META, STAFF_EXPECTED_META } from './api';
import { ClassLog, type LogAudience } from './ClassLog';
import { ProgressControls } from './ProgressControls';
import { ProgressDashboard } from './ProgressDashboard';
import { useSessionLog, useStudentProgress } from './queries';
import {
  DEFAULT_CONTROLS,
  isConsentRequired,
  isNotFound,
  parseProgressControls,
  RANGE_ERROR_KEYS,
  rangeErrorOf,
  rangeFor,
  subjectOptions,
  withProgressControls,
  type ProgressControls as Controls,
} from './rules';

// One student's progress (#322 B, C, D), shared by both pages: the
// controls in the URL (the range in the center zone), the summary and the
// class log requested in parallel with the same range and subject, and
// every state:
// - loading → LoadingState; an error → ErrorAlert + "Try again";
// - a 422 range or subject refusal → its message + "Reset filters";
// - families: a 403 consent → ConsentRequired; a 404 → "This progress
//   page isn't available." inline (the picker stays usable);
// - staff and tutors (E2): progress 404 with the log 200 → the former
//   tutor's view (a status line, the period, the class log only); both
//   404 → "This progress page isn't available.".
// "Clear filters" and "Reset filters" move focus to the page heading once
// the new reads have settled.

export function ProgressView({
  studentId,
  audience,
  studentName,
  headingRef,
  links,
  showsSessionLink = false,
  chartWidth,
}: {
  studentId: string;
  audience: LogAudience;
  /** For the consent message. */
  studentName: string;
  headingRef: RefObject<HTMLHeadingElement | null>;
  /** The related links (F1), shown with the full page only. */
  links: ReactNode;
  showsSessionLink?: boolean;
  chartWidth?: number;
}) {
  const { t } = useTranslation();
  const { t: tErrors } = useTranslation('errors');
  const [params, setParams] = useSearchParams();
  const controls = parseProgressControls(params);
  const timeZone = useCenterTimeZone();
  const range = rangeFor(controls.months, todayInZone(timeZone));
  const query = { from: range.from, to: range.to, subject: controls.subject };
  const meta =
    audience === 'family' ? FAMILY_EXPECTED_META : STAFF_EXPECTED_META;
  const progress = useStudentProgress(studentId, query, true, meta);
  const { log, state: logState } = useSessionLog(studentId, query, true, meta);
  // After "Clear filters" or "Reset filters": the heading takes focus once
  // the new reads have settled (the button may be gone by then).
  const focusPending = useRef(false);
  const [resets, setResets] = useState(0);
  const settled = !progress.isFetching && !log.isFetching;

  useEffect(() => {
    if (focusPending.current && settled) {
      focusPending.current = false;
      headingRef.current?.focus();
    }
  }, [resets, settled, headingRef]);

  function change(next: Controls) {
    setParams((previous) => withProgressControls(previous, next));
  }

  function reset() {
    change(DEFAULT_CONTROLS);
    focusPending.current = true;
    setResets((count) => count + 1);
  }

  const classLog = (
    <ClassLog
      state={logState}
      audience={audience}
      studentId={studentId}
      showsSessionLink={showsSessionLink}
    />
  );

  if (progress.isError) {
    const { error } = progress;
    if (audience === 'family' && isConsentRequired(error)) {
      return (
        <ConsentRequired
          name={studentName}
          title={t('academics.history.consentTitle')}
        />
      );
    }
    const rangeError = rangeErrorOf(error);
    if (rangeError) {
      return (
        <ErrorAlert
          error={error}
          message={tErrors(RANGE_ERROR_KEYS[rangeError])}
          retryLabel={t('progress.controls.reset')}
          onRetry={reset}
        />
      );
    }
    if (isNotFound(error)) {
      if (audience === 'staff' && log.data) {
        // E2: a former tutor reads the classes they taught.
        return (
          <div className="flex min-w-0 flex-col gap-6">
            <p role="status" className="text-sm">
              {t('progress.formerTutor')}
            </p>
            <ProgressControls
              controls={controls}
              onChange={change}
              onClear={reset}
              subjects={null}
              range={range}
            />
            {classLog}
          </div>
        );
      }
      if (audience === 'staff' && !log.isError) {
        return <LoadingState rows={4} />;
      }
      if (audience === 'staff' && !isNotFound(log.error)) {
        return (
          <ErrorAlert error={log.error} onRetry={() => void log.refetch()} />
        );
      }
      return (
        <p role="status" className="text-sm">
          {t('progress.unavailable')}
        </p>
      );
    }
    return <ErrorAlert error={error} onRetry={() => void progress.refetch()} />;
  }
  if (!progress.data) {
    return <LoadingState rows={6} />;
  }
  return (
    <div className="flex min-w-0 flex-col gap-6">
      {links}
      <ProgressControls
        controls={controls}
        onChange={change}
        onClear={reset}
        subjects={subjectOptions(progress.data)}
        range={range}
      />
      <div aria-busy={progress.isPlaceholderData} className="min-w-0">
        <ProgressDashboard progress={progress.data} chartWidth={chartWidth} />
      </div>
      {classLog}
    </div>
  );
}
