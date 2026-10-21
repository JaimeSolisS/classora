import { useQuery } from '@tanstack/react-query';
import { X } from 'lucide-react';
import { useId } from 'react';
import { useTranslation } from 'react-i18next';
import { Button } from '@/components/ui/button';
import { Checkbox } from '@/components/ui/checkbox';
import { SearchPicker } from '@/features/guardians/SearchPicker';
import { SELECT_CLASS } from '@/features/students/styles';
import { CALENDAR_KEY, type CalendarLoaders, type FilterOption } from './api';
import {
  hasFilters,
  NO_FILTERS,
  SESSION_KINDS,
  type CalendarFilters as Filters,
  type SessionKindFilter,
} from './urlState';

// The calendar's filters (#57 D6): tutor and student search pickers, a room
// select, the class type and "Show cancelled". Active filters show as
// removable chips. The tutor options come from the schedule.view route
// (#57 E2), so Finance can filter by tutor too.

export interface CalendarFiltersProps {
  filters: Filters;
  onChange: (filters: Filters) => void;
  loaders: Pick<
    CalendarLoaders,
    'searchTutors' | 'searchStudents' | 'fetchRooms'
  >;
  /** Names of the chosen tutor and student (by id), for the chips. */
  names: Readonly<Record<string, string>>;
  onName: (id: string, name: string) => void;
}

export function CalendarFilters({
  filters,
  onChange,
  loaders,
  names,
  onName,
}: CalendarFiltersProps) {
  const { t } = useTranslation();
  const id = useId();
  const rooms = useQuery({
    queryKey: [...CALENDAR_KEY, 'rooms'],
    queryFn: ({ signal }) => loaders.fetchRooms(signal),
  });
  const label = (option: FilterOption): string =>
    option.inactive
      ? t('scheduling.calendar.filters.inactive', { name: option.name })
      : option.name;
  const roomName = (roomId: string) => {
    const room = rooms.data?.find((option) => option.id === roomId);
    return room ? label(room) : t('scheduling.calendar.filters.selected');
  };
  const nameOf = (value: string) =>
    names[value] ?? t('scheduling.calendar.filters.selected');

  const chips: { key: keyof Filters; filter: string; value: string }[] = [];
  if (filters.tutor) {
    chips.push({
      key: 'tutor',
      filter: t('scheduling.calendar.filters.tutor'),
      value: nameOf(filters.tutor),
    });
  }
  if (filters.student) {
    chips.push({
      key: 'student',
      filter: t('scheduling.calendar.filters.student'),
      value: nameOf(filters.student),
    });
  }
  if (filters.room) {
    chips.push({
      key: 'room',
      filter: t('scheduling.calendar.filters.room'),
      value: roomName(filters.room),
    });
  }
  if (filters.kind) {
    chips.push({
      key: 'kind',
      filter: t('scheduling.calendar.filters.kind'),
      value: t(`scheduling.calendar.filters.kinds.${filters.kind}`),
    });
  }
  if (filters.cancelled) {
    chips.push({
      key: 'cancelled',
      filter: t('scheduling.calendar.filters.cancelled'),
      value: '',
    });
  }

  const remove = (key: keyof Filters) =>
    onChange({ ...filters, [key]: NO_FILTERS[key] });

  return (
    <section
      aria-labelledby={`${id}-heading`}
      className="flex flex-col gap-4 rounded-lg border border-border p-4"
    >
      <h2 id={`${id}-heading`} className="text-lg font-semibold">
        {t('scheduling.calendar.filters.label')}
      </h2>
      <div className="grid gap-4 md:grid-cols-2">
        <SearchPicker<FilterOption>
          label={t('scheduling.calendar.filters.tutor')}
          help={t('scheduling.calendar.filters.tutorHelp')}
          queryKey={[...CALENDAR_KEY, 'tutors']}
          search={loaders.searchTutors}
          optionId={(option) => option.id}
          optionLabel={label}
          selectedId={filters.tutor}
          onSelect={(option) => {
            onName(option.id, label(option));
            onChange({ ...filters, tutor: option.id });
          }}
        />
        <SearchPicker<FilterOption>
          label={t('scheduling.calendar.filters.student')}
          help={t('scheduling.calendar.filters.studentHelp')}
          queryKey={[...CALENDAR_KEY, 'students']}
          search={loaders.searchStudents}
          optionId={(option) => option.id}
          optionLabel={label}
          selectedId={filters.student}
          onSelect={(option) => {
            onName(option.id, label(option));
            onChange({ ...filters, student: option.id });
          }}
        />
      </div>
      <div className="grid gap-4 sm:grid-cols-2 md:grid-cols-3 md:items-end">
        <div className="flex flex-col gap-2">
          <label htmlFor={`${id}-room`} className="text-sm font-medium">
            {t('scheduling.calendar.filters.room')}
          </label>
          <select
            id={`${id}-room`}
            className={SELECT_CLASS}
            value={filters.room ?? ''}
            aria-describedby={rooms.isError ? `${id}-room-error` : undefined}
            onChange={(event) =>
              onChange({ ...filters, room: event.target.value || null })
            }
          >
            <option value="">
              {t('scheduling.calendar.filters.allRooms')}
            </option>
            {filters.room && !rooms.data?.some((r) => r.id === filters.room) ? (
              <option value={filters.room}>{roomName(filters.room)}</option>
            ) : null}
            {(rooms.data ?? []).map((room) => (
              <option key={room.id} value={room.id}>
                {label(room)}
              </option>
            ))}
          </select>
          {rooms.isError ? (
            <p id={`${id}-room-error`} className="text-sm text-destructive">
              {t('scheduling.calendar.filters.roomsFailed')}
            </p>
          ) : null}
        </div>
        <div className="flex flex-col gap-2">
          <label htmlFor={`${id}-kind`} className="text-sm font-medium">
            {t('scheduling.calendar.filters.kind')}
          </label>
          <select
            id={`${id}-kind`}
            className={SELECT_CLASS}
            value={filters.kind ?? ''}
            onChange={(event) =>
              onChange({
                ...filters,
                kind: (event.target.value || null) as SessionKindFilter | null,
              })
            }
          >
            <option value="">
              {t('scheduling.calendar.filters.kinds.all')}
            </option>
            {SESSION_KINDS.map((kind) => (
              <option key={kind} value={kind}>
                {t(`scheduling.calendar.filters.kinds.${kind}`)}
              </option>
            ))}
          </select>
        </div>
        <div className="flex min-h-9 items-center gap-3">
          <Checkbox
            id={`${id}-cancelled`}
            checked={filters.cancelled}
            onCheckedChange={(checked) =>
              onChange({ ...filters, cancelled: checked === true })
            }
          />
          <label htmlFor={`${id}-cancelled`} className="text-sm font-medium">
            {t('scheduling.calendar.filters.cancelled')}
          </label>
        </div>
      </div>
      <div className="flex flex-wrap items-center gap-2">
        {chips.length > 0 ? (
          <ul
            aria-label={t('scheduling.calendar.filters.active')}
            className="flex flex-wrap gap-2"
          >
            {chips.map((chip) => (
              <li key={chip.key}>
                <Button
                  type="button"
                  variant="secondary"
                  size="sm"
                  aria-label={t('scheduling.calendar.filters.remove', {
                    filter: chip.filter,
                    value: chip.value,
                  }).trim()}
                  onClick={() => remove(chip.key)}
                >
                  {chip.value ? `${chip.filter}: ${chip.value}` : chip.filter}
                  <X aria-hidden="true" />
                </Button>
              </li>
            ))}
          </ul>
        ) : null}
        <Button
          type="button"
          variant="outline"
          size="sm"
          disabled={!hasFilters(filters)}
          onClick={() => onChange(NO_FILTERS)}
        >
          {t('scheduling.calendar.filters.clear')}
        </Button>
      </div>
    </section>
  );
}
