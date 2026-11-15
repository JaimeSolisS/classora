import { useId } from 'react';
import { useTranslation } from 'react-i18next';
import { Button } from '@/components/ui/button';
import { SELECT_CLASS } from '@/features/students/styles';
import { useFormatters } from '@/lib/formatters';
import type { ProgressSubject } from './api';
import {
  hasProgressFilters,
  MONTH_PRESETS,
  type ProgressControls as Controls,
} from './rules';

// The page's controls (#322 B2, B3): "Period" (four aria-pressed presets,
// 12 months by default) and "Subject" ("All subjects" plus the subjects of
// the unfiltered range, AM1), stacked in one column below sm. "Clear
// filters" shows while either differs from the default. The former tutor's
// view has the period only (D2). The range shown is the one requested.

export function ProgressControls({
  controls,
  onChange,
  onClear,
  subjects,
  range,
}: {
  controls: Controls;
  onChange: (controls: Controls) => void;
  /** Back to the defaults (the page then moves focus to its heading). */
  onClear: () => void;
  /** null: no subject filter (the former tutor's view). */
  subjects: readonly ProgressSubject[] | null;
  range: { from: string; to: string };
}) {
  const { t } = useTranslation();
  const { formatLocalDate } = useFormatters();
  const id = useId();
  const filtered = hasProgressFilters(
    subjects === null ? { ...controls, subject: null } : controls,
  );
  return (
    <div className="flex min-w-0 flex-col gap-3">
      <div className="flex min-w-0 flex-col gap-3 sm:flex-row sm:flex-wrap sm:items-end">
        <div className="flex min-w-0 flex-col gap-1.5">
          <span id={`${id}-period`} className="text-sm font-medium">
            {t('progress.controls.period')}
          </span>
          <div
            role="group"
            aria-labelledby={`${id}-period`}
            className="flex flex-wrap gap-1"
          >
            {MONTH_PRESETS.map((months) => (
              <Button
                key={months}
                type="button"
                size="sm"
                variant={controls.months === months ? 'default' : 'outline'}
                aria-pressed={controls.months === months}
                onClick={() => onChange({ ...controls, months })}
              >
                {t('progress.controls.months', { count: months })}
              </Button>
            ))}
          </div>
        </div>
        {subjects !== null ? (
          <div className="flex min-w-0 flex-col gap-1.5">
            <label htmlFor={`${id}-subject`} className="text-sm font-medium">
              {t('progress.controls.subject')}
            </label>
            <select
              id={`${id}-subject`}
              className={`${SELECT_CLASS} sm:w-64`}
              value={controls.subject ?? ''}
              onChange={(event) =>
                onChange({
                  ...controls,
                  subject: event.target.value || null,
                })
              }
            >
              <option value="">{t('progress.controls.allSubjects')}</option>
              {subjects.map((subject) => (
                <option key={subject.id} value={subject.id}>
                  {subject.name}
                </option>
              ))}
            </select>
          </div>
        ) : null}
        {filtered ? (
          <Button
            type="button"
            variant="outline"
            className="self-start sm:self-end"
            onClick={onClear}
          >
            {t('progress.controls.clear')}
          </Button>
        ) : null}
      </div>
      <p className="text-sm text-muted-foreground">
        {t('progress.controls.range', {
          from: formatLocalDate(range.from),
          to: formatLocalDate(range.to),
        })}
      </p>
    </div>
  );
}
