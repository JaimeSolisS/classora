import { useId } from 'react';
import { useTranslation } from 'react-i18next';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { FieldError } from '@/features/auth/FieldError';
import { INPUT_CLASS } from '@/features/payroll/sheet';
import type { ReportBasis } from './api';
import {
  BASES,
  PRESETS,
  type DateRange,
  type Preset,
  type RangeField,
} from './rules';

// The report's controls (M7-14b B1, E2): the basis switch, the presets and
// the custom range. A broken bound shows next to the field it names, from
// the client check or the server's `422 report_range_invalid`.

export interface RangeControlProps {
  basis: ReportBasis;
  onBasis: (basis: ReportBasis) => void;
  /** The dates as typed (the URL's until the user edits them). */
  draft: DateRange;
  onDate: (field: RangeField, value: string) => void;
  /** The preset whose range is the current one, if any. */
  preset: Preset | null;
  onPreset: (preset: Preset) => void;
  /** The center's today (the dates can't go past it). */
  today: string;
  /** A translated message next to the field it names, or null. */
  error: { field: RangeField; message: string } | null;
}

const L = 'reports.profitability';

export function RangeControl({
  basis,
  onBasis,
  draft,
  onDate,
  preset,
  onPreset,
  today,
  error,
}: RangeControlProps) {
  const { t } = useTranslation();
  const id = useId();
  const ids = {
    basis: `${id}-basis`,
    presets: `${id}-presets`,
    from: `${id}-from`,
    to: `${id}-to`,
    fromError: `${id}-from-error`,
    toError: `${id}-to-error`,
  };
  const fieldError = (field: RangeField) =>
    error?.field === field ? error.message : undefined;

  return (
    <div
      role="group"
      aria-label={t(`${L}.controls`)}
      className="flex min-w-0 flex-col gap-4"
      data-report-controls
    >
      <div className="flex min-w-0 flex-col gap-2">
        <span id={ids.basis} className="text-sm font-medium">
          {t(`${L}.basis.label`)}
        </span>
        <div
          role="group"
          aria-labelledby={ids.basis}
          className="flex flex-wrap gap-2"
          data-report-basis
        >
          {BASES.map((item) => (
            <Button
              key={item}
              type="button"
              variant={item === basis ? 'default' : 'outline'}
              aria-pressed={item === basis}
              className="min-h-11 md:min-h-9"
              onClick={() => onBasis(item)}
            >
              {t(`${L}.basis.${item}`)}
            </Button>
          ))}
        </div>
      </div>
      <div className="flex min-w-0 flex-col gap-2">
        <span id={ids.presets} className="text-sm font-medium">
          {t(`${L}.presets.label`)}
        </span>
        <div
          role="group"
          aria-labelledby={ids.presets}
          className="flex flex-wrap gap-2"
          data-report-presets
        >
          {PRESETS.map((item) => (
            <Button
              key={item}
              type="button"
              variant={item === preset ? 'secondary' : 'outline'}
              aria-pressed={item === preset}
              className="min-h-11 md:min-h-9"
              onClick={() => onPreset(item)}
            >
              {t(`${L}.presets.${item}`)}
            </Button>
          ))}
        </div>
      </div>
      <fieldset className="flex min-w-0 flex-col gap-2">
        <legend className="mb-2 text-sm font-medium">
          {t(`${L}.range.label`)}
        </legend>
        <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
          {(['from', 'to'] as const).map((field) => {
            const message = fieldError(field);
            const errorId = field === 'from' ? ids.fromError : ids.toError;
            return (
              <div key={field} className="flex min-w-0 flex-col gap-2">
                <label htmlFor={ids[field]} className="text-sm font-medium">
                  {t(`${L}.range.${field}`)}
                </label>
                <Input
                  id={ids[field]}
                  type="date"
                  className={INPUT_CLASS}
                  max={today}
                  value={draft[field]}
                  aria-invalid={message ? true : undefined}
                  aria-describedby={message ? errorId : undefined}
                  data-report-date={field}
                  onChange={(event) => onDate(field, event.target.value)}
                />
                <FieldError id={errorId} message={message} />
              </div>
            );
          })}
        </div>
      </fieldset>
    </div>
  );
}
