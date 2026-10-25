import { X } from 'lucide-react';
import { useId } from 'react';
import { useTranslation } from 'react-i18next';
import { Link } from 'react-router';
import { ErrorAlert } from '@/components/feedback/ErrorAlert';
import { LoadingState } from '@/components/feedback/LoadingState';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { FieldError } from '@/features/auth/FieldError';
import type { FilterOption } from '@/features/calendar/api';
import { Pagination } from '@/features/catalog/Pagination';
import { SearchPicker } from '@/features/guardians/SearchPicker';
import { toLanguage } from '@/i18n/language';
import { formatElapsed, formatInstant, type LocalDate } from '@/lib/time';
import {
  OVERDUE_CLOSEOUTS_KEY,
  OVERDUE_PAGE_SIZE,
  type OverdueCloseoutItem,
} from './api';
import {
  hasOverdueFilters,
  NO_OVERDUE_FILTERS,
  rangeInvalid,
  type OverdueState,
} from './urlState';

// "Overdue closeouts" (#301b A), presentational: the classes that have
// ended without a closeout, in the API's order (oldest first). A real table
// (the h1 names it) with the date and time in the center zone, "Ended
// {relative}", the subject and level, the tutor ("Can't submit" as a text
// badge when the tutor can't act), the kind, the participants, the
// attendance marked and "Open session" (to the session's Closeout section).
// No student names. The tutor (search picker over the schedule's tutor
// options) and the date range filter the list; `to` before `from` is a
// field error and nothing is read. On a phone the table scrolls inside its
// own focusable region (#311) and the page never scrolls sideways.

export type OverdueCloseoutsState =
  | { status: 'loading' }
  | { status: 'error'; error: unknown; onRetry: () => void }
  | { status: 'invalid' }
  | {
      status: 'ready';
      items: readonly OverdueCloseoutItem[];
      total: number;
      offset: number;
      /** When the page was read ("Ended 3 hours ago"). */
      fetchedAt: Date;
    };

export interface OverdueCloseoutsViewProps {
  state: OverdueCloseoutsState;
  filters: OverdueState;
  onFilters: (next: OverdueState) => void;
  timeZone: string;
  searchTutors: (q: string, signal: AbortSignal) => Promise<FilterOption[]>;
  /** The chosen tutor's name, for the chip. */
  tutorName: string | null;
  /** Remembers a picked tutor's name by id. */
  onTutorName: (id: string, name: string) => void;
  /** The server refused the range (422 range_invalid). */
  rangeError?: boolean;
}

const LINK_CLASS =
  'text-sm underline underline-offset-4 outline-none focus-visible:ring-3 focus-visible:ring-ring';

export function OverdueCloseoutsView({
  state,
  filters,
  onFilters,
  timeZone,
  searchTutors,
  tutorName,
  onTutorName,
  rangeError = false,
}: OverdueCloseoutsViewProps) {
  const { t, i18n } = useTranslation();
  const language = toLanguage(i18n.resolvedLanguage);
  const id = useId();
  const headingId = `${id}-heading`;
  const filtered = hasOverdueFilters(filters);
  const invalidRange = rangeError || rangeInvalid(filters);

  /** Any filter change goes back to page 1 (A5). */
  function change(next: Partial<OverdueState>) {
    onFilters({ ...filters, ...next, page: 1 });
  }

  const optionLabel = (option: FilterOption): string =>
    option.inactive
      ? t('scheduling.calendar.filters.inactive', { name: option.name })
      : option.name;
  const chipName = tutorName ?? t('scheduling.overdue.filters.selected');
  const clear = (
    <Button
      type="button"
      variant="outline"
      size="sm"
      onClick={() => onFilters({ ...NO_OVERDUE_FILTERS, page: 1 })}
    >
      {t('scheduling.overdue.filters.clear')}
    </Button>
  );

  let content;
  if (state.status === 'loading') {
    content = <LoadingState layout="table" rows={4} />;
  } else if (state.status === 'error') {
    content = <ErrorAlert error={state.error} onRetry={state.onRetry} />;
  } else if (state.status === 'invalid') {
    content = null;
  } else if (state.items.length === 0) {
    content = (
      <div className="flex flex-col items-start gap-2">
        <p role="status" className="text-sm">
          {filtered
            ? t('scheduling.overdue.emptyFiltered')
            : t('scheduling.overdue.empty')}
        </p>
        {filtered ? clear : null}
      </div>
    );
  } else {
    const { items, fetchedAt } = state;
    content = (
      <>
        <div
          role="region"
          aria-labelledby={headingId}
          tabIndex={0}
          className="relative min-w-0 overflow-x-auto rounded-sm outline-none focus-visible:ring-3 focus-visible:ring-ring"
        >
          <table
            aria-labelledby={headingId}
            className="w-full min-w-[48rem] border-collapse text-left text-sm"
          >
            <thead>
              <tr className="border-b border-border">
                <th scope="col" className="py-2 pr-3 font-medium">
                  {t('scheduling.overdue.columns.date')}
                </th>
                <th scope="col" className="py-2 pr-3 font-medium">
                  {t('scheduling.overdue.columns.ended')}
                </th>
                <th scope="col" className="py-2 pr-3 font-medium">
                  {t('scheduling.overdue.columns.subject')}
                </th>
                <th scope="col" className="py-2 pr-3 font-medium">
                  {t('scheduling.overdue.columns.tutor')}
                </th>
                <th scope="col" className="py-2 pr-3 font-medium">
                  {t('scheduling.overdue.columns.kind')}
                </th>
                <th scope="col" className="py-2 pr-3 font-medium">
                  {t('scheduling.overdue.columns.participants')}
                </th>
                <th scope="col" className="py-2 pr-3 font-medium">
                  {t('scheduling.overdue.columns.attendance')}
                </th>
                <th scope="col" className="py-2 font-medium">
                  <span className="sr-only">
                    {t('scheduling.overdue.columns.action')}
                  </span>
                </th>
              </tr>
            </thead>
            <tbody>
              {items.map((item) => {
                const when = formatInstant(item.starts_at, {
                  timeZone,
                  language,
                  style: 'dateTime',
                });
                const end = formatInstant(item.ends_at, {
                  timeZone,
                  language,
                  style: 'time',
                });
                return (
                  <tr
                    key={item.class_session_id}
                    className="border-b border-border align-top"
                  >
                    <td className="py-2 pr-3">
                      {t('scheduling.overdue.when', { when, end })}
                    </td>
                    <td className="py-2 pr-3">
                      {t('scheduling.overdue.ended', {
                        elapsed: formatElapsed(
                          item.ends_at,
                          fetchedAt,
                          language,
                        ),
                      })}
                    </td>
                    <td className="py-2 pr-3 break-words">
                      <span className="block">{item.subject.name}</span>
                      <span className="block text-muted-foreground">
                        {item.subject.education_level_name}
                      </span>
                    </td>
                    <td className="py-2 pr-3 break-words">
                      <span className="block">{item.tutor.display_name}</span>
                      {item.tutor.can_submit ? null : (
                        <span className="mt-1 inline-flex rounded-md border border-border px-2 py-0.5 text-xs font-medium">
                          {t('scheduling.overdue.cantSubmit')}
                        </span>
                      )}
                    </td>
                    <td className="py-2 pr-3">
                      {t(`scheduling.calendar.filters.kinds.${item.kind}`)}
                    </td>
                    <td className="py-2 pr-3">
                      {t('scheduling.overdue.participants', {
                        count: item.participant_count,
                      })}
                    </td>
                    <td className="py-2 pr-3">
                      {t('scheduling.overdue.attendance', {
                        marked: item.attendance.marked,
                        total: item.attendance.total,
                      })}
                    </td>
                    <td className="py-2">
                      <Link
                        to={`/schedule/sessions/${encodeURIComponent(item.class_session_id)}#closeout`}
                        aria-label={t('scheduling.overdue.openFor', {
                          subject: item.subject.name,
                          when,
                        })}
                        className={`${LINK_CLASS} whitespace-nowrap`}
                      >
                        {t('scheduling.overdue.open')}
                      </Link>
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
        {state.total > OVERDUE_PAGE_SIZE ? (
          <Pagination
            offset={state.offset}
            limit={OVERDUE_PAGE_SIZE}
            shown={items.length}
            total={state.total}
            onPage={(page) => onFilters({ ...filters, page })}
          />
        ) : null}
      </>
    );
  }

  return (
    <div className="flex min-w-0 flex-col gap-6">
      <div className="flex min-w-0 flex-col gap-2">
        <h1 id={headingId} className="text-2xl font-semibold">
          {t('scheduling.overdue.title')}
        </h1>
        <p className="max-w-3xl text-sm text-muted-foreground">
          {t('scheduling.overdue.intro')}
        </p>
        <Link to="/schedule" className={`${LINK_CLASS} self-start`}>
          {t('scheduling.overdue.back')}
        </Link>
      </div>
      <section
        aria-labelledby={`${id}-filters`}
        className="flex min-w-0 flex-col gap-4 rounded-lg border border-border p-4"
      >
        <h2 id={`${id}-filters`} className="text-lg font-semibold">
          {t('scheduling.overdue.filters.label')}
        </h2>
        <div className="grid min-w-0 gap-4 sm:grid-cols-2 lg:grid-cols-3">
          <div className="min-w-0 sm:col-span-2 lg:col-span-1">
            <SearchPicker<FilterOption>
              label={t('scheduling.overdue.filters.tutor')}
              help={t('scheduling.calendar.filters.tutorHelp')}
              queryKey={[...OVERDUE_CLOSEOUTS_KEY, 'tutors']}
              search={searchTutors}
              optionId={(option) => option.id}
              optionLabel={optionLabel}
              selectedId={filters.tutor}
              onSelect={(option) => {
                onTutorName(option.id, optionLabel(option));
                change({ tutor: option.id });
              }}
            />
          </div>
          <DateField
            label={t('scheduling.overdue.filters.from')}
            value={filters.from}
            onChange={(from) => change({ from })}
          />
          <DateField
            label={t('scheduling.overdue.filters.to')}
            value={filters.to}
            onChange={(to) => change({ to })}
            error={
              invalidRange
                ? t('scheduling.overdue.filters.rangeInvalid')
                : undefined
            }
          />
        </div>
        {filtered ? (
          <div className="flex flex-wrap items-center gap-2">
            {filters.tutor ? (
              <Button
                type="button"
                variant="secondary"
                size="sm"
                className="h-auto min-h-8 whitespace-normal"
                aria-label={t('scheduling.overdue.filters.remove', {
                  name: chipName,
                })}
                onClick={() => change({ tutor: null })}
              >
                {t('scheduling.overdue.filters.chip', { name: chipName })}
                <X aria-hidden="true" />
              </Button>
            ) : null}
            {clear}
          </div>
        ) : null}
      </section>
      {content}
    </div>
  );
}

function DateField({
  label,
  value,
  onChange,
  error,
}: {
  label: string;
  value: LocalDate | null;
  onChange: (value: LocalDate | null) => void;
  error?: string;
}) {
  const id = useId();
  return (
    <div className="flex min-w-0 flex-col gap-2">
      <label htmlFor={id} className="text-sm font-medium">
        {label}
      </label>
      <Input
        id={id}
        type="date"
        value={value ?? ''}
        aria-invalid={error ? true : undefined}
        aria-describedby={error ? `${id}-error` : undefined}
        onChange={(event) =>
          onChange(
            /^\d{4}-\d{2}-\d{2}$/.test(event.target.value)
              ? (event.target.value as LocalDate)
              : null,
          )
        }
      />
      <FieldError id={`${id}-error`} message={error} />
    </div>
  );
}
