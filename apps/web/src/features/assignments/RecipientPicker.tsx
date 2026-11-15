import { useQuery, useQueryClient } from '@tanstack/react-query';
import { useEffect, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { Button } from '@/components/ui/button';
import { Checkbox } from '@/components/ui/checkbox';
import { Input } from '@/components/ui/input';
import { FieldError } from '@/features/auth/FieldError';
import {
  ASSIGNMENTS_KEY,
  fetchPickerStudents,
  pickerKey,
  type StudentListPage,
} from './api';

// The recipients of a homework (#313 B3): a fieldset "Students" with one
// labelled checkbox per student, "{n} selected" (a polite live region),
// "Select all" and "Clear". Without a fixed list it offers the tutor's
// students from GET /students (for a tutor, exactly their current
// students), with a search that sends `q` after the students list's
// debounce; past 100 matches a note says to search (no "Load more"). With a
// fixed list (a class's participants) only those. `base` students (the
// current recipients on an edit) are always listed; a `locked` one (work
// handed in) stays checked, with the reason "Already handed in". A student
// the server refused carries its message on the checkbox.

export interface PickerStudent {
  id: string;
  name: string;
}

export interface RecipientPickerProps {
  idPrefix: string;
  value: readonly string[];
  onChange: (next: string[]) => void;
  /** A class's participants: the only options, no search. */
  fixed: readonly PickerStudent[] | null;
  /** Always listed (the current recipients on an edit). */
  base?: readonly PickerStudent[];
  locked?: ReadonlySet<string>;
  /** The fieldset's message (none, too many, a refused save's summary). */
  error?: string;
  /** Per-student messages (a refused save). */
  studentErrors?: ReadonlyMap<string, string>;
  /** Shown under the fieldset (edit: "Added students are notified."). */
  note?: string;
}

/** The search waits as the students list's does (#33). */
export const SEARCH_DEBOUNCE_MS = 300;

const fullName = (item: { given_names: string; family_names: string }) =>
  `${item.given_names} ${item.family_names}`;

export function RecipientPicker({
  idPrefix,
  value,
  onChange,
  fixed,
  base = [],
  locked = new Set(),
  error,
  studentErrors = new Map(),
  note,
}: RecipientPickerProps) {
  const { t } = useTranslation();
  const [typed, setTyped] = useState('');
  const [q, setQ] = useState('');
  const client = useQueryClient();
  useEffect(() => {
    const timer = window.setTimeout(
      () => setQ(typed.trim()),
      SEARCH_DEBOUNCE_MS,
    );
    return () => window.clearTimeout(timer);
  }, [typed]);
  const search = useQuery({
    queryKey: pickerKey(q),
    queryFn: ({ signal }) => fetchPickerStudents(q, signal),
    enabled: fixed === null,
    retry: false,
  });

  const found: readonly PickerStudent[] =
    fixed ??
    (search.data?.items ?? []).map((item) => ({
      id: item.id,
      name: fullName(item),
    }));
  // Names of checked students a search hides: every page read so far.
  const known = new Map<string, string>();
  for (const [, page] of client.getQueriesData<StudentListPage>({
    queryKey: [...ASSIGNMENTS_KEY, 'picker'],
  })) {
    for (const item of page?.items ?? []) {
      known.set(item.id, fullName(item));
    }
  }
  for (const student of [...base, ...found]) {
    known.set(student.id, student.name);
  }
  const listed: PickerStudent[] = [];
  const seen = new Set<string>();
  const add = (student: PickerStudent) => {
    if (!seen.has(student.id)) {
      seen.add(student.id);
      listed.push(student);
    }
  };
  base.forEach(add);
  // Checked students stay listed while a search hides them.
  for (const id of value) {
    const name = known.get(id);
    if (name !== undefined) {
      add({ id, name });
    }
  }
  found.forEach(add);
  const truncated =
    fixed === null &&
    search.data !== undefined &&
    search.data.total > search.data.items.length;

  const checked = new Set(value);
  const toggle = (id: string, on: boolean) => {
    if (locked.has(id)) {
      return;
    }
    onChange(on ? [...value, id] : value.filter((selected) => selected !== id));
  };
  const id = (part: string) => `${idPrefix}-${part}`;

  return (
    <fieldset
      className="flex min-w-0 flex-col gap-3"
      aria-describedby={error ? id('error') : undefined}
      data-recipients=""
    >
      <legend className="text-sm font-medium">
        {t('assignments.form.students')}
      </legend>
      {fixed === null ? (
        <div className="flex min-w-0 flex-col gap-1.5">
          <label htmlFor={id('search')} className="text-sm">
            {t('assignments.form.search')}
          </label>
          <Input
            id={id('search')}
            type="search"
            autoComplete="off"
            value={typed}
            onChange={(event) => setTyped(event.target.value)}
          />
        </div>
      ) : null}
      <div className="flex flex-wrap items-center gap-2">
        <Button
          type="button"
          variant="outline"
          size="sm"
          onClick={() =>
            onChange([
              ...new Set([...value, ...listed.map((student) => student.id)]),
            ])
          }
        >
          {t('assignments.form.selectAll')}
        </Button>
        <Button
          type="button"
          variant="outline"
          size="sm"
          onClick={() =>
            onChange(value.filter((selected) => locked.has(selected)))
          }
        >
          {t('assignments.form.clear')}
        </Button>
        <p role="status" aria-live="polite" className="text-sm">
          {t('assignments.form.selected', { count: value.length })}
        </p>
      </div>
      {error ? (
        <p id={id('error')} role="alert" className="text-sm text-destructive">
          {error}
        </p>
      ) : null}
      {truncated ? (
        <p className="text-sm text-muted-foreground">
          {t('assignments.form.truncated', {
            max: search.data?.items.length ?? 0,
          })}
        </p>
      ) : null}
      {fixed === null && search.isError ? (
        <p role="alert" className="text-sm text-destructive">
          {t('assignments.form.studentsFailed')}
        </p>
      ) : null}
      {listed.length === 0 && (fixed !== null || search.data) ? (
        <p className="text-sm text-muted-foreground">
          {t('assignments.form.noStudents')}
        </p>
      ) : null}
      <ul className="flex min-w-0 flex-col gap-1">
        {listed.map((student) => {
          const boxId = id(`s-${student.id}`);
          const isLocked = locked.has(student.id);
          const message = studentErrors.get(student.id);
          const described = [
            isLocked ? `${boxId}-locked` : '',
            message ? `${boxId}-error` : '',
          ]
            .filter(Boolean)
            .join(' ');
          return (
            <li key={student.id} className="flex min-w-0 flex-col gap-0.5 py-1">
              <div className="flex min-w-0 items-center gap-3">
                <Checkbox
                  id={boxId}
                  data-student={student.id}
                  checked={checked.has(student.id)}
                  disabled={isLocked}
                  aria-invalid={message ? true : undefined}
                  aria-describedby={described || undefined}
                  onCheckedChange={(next) => toggle(student.id, next === true)}
                />
                <label
                  htmlFor={boxId}
                  className="min-w-0 py-1 text-sm break-words"
                >
                  {student.name}
                </label>
              </div>
              {isLocked ? (
                <p
                  id={`${boxId}-locked`}
                  className="pl-7 text-sm text-muted-foreground"
                >
                  {t('assignments.form.alreadyHandedIn')}
                </p>
              ) : null}
              <div className="pl-7">
                <FieldError id={`${boxId}-error`} message={message} />
              </div>
            </li>
          );
        })}
      </ul>
      {note ? <p className="text-sm text-muted-foreground">{note}</p> : null}
    </fieldset>
  );
}
