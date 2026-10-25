import { useQuery } from '@tanstack/react-query';
import { useId } from 'react';
import { useTranslation } from 'react-i18next';
import { ErrorAlert } from '@/components/feedback/ErrorAlert';
import {
  familyNotesKey,
  fetchFamilyNotes,
  type FamilyLearningNote,
} from '@/features/tutor-portal/notes/api';
import { toLanguage } from '@/i18n/language';
import { formatInstant } from '@/lib/time';

// "Notes from the tutor" in a family's My schedule dialog (#79 H2): the
// notes the tutor shared about this student in this class, read from the
// family route (shared notes only) when the dialog opens. Each shows the
// text (plain text), the tutor's name and the date it was shared. Nothing
// is rendered while loading or when there are none; a failure shows an
// inline error with "Try again". `notes` and `error` are for stories.

export interface FamilyNotesSectionProps {
  studentId: string;
  sessionId: string;
  timeZone: string;
}

export function FamilyNotesSection({
  studentId,
  sessionId,
  timeZone,
}: FamilyNotesSectionProps) {
  const query = useQuery({
    queryKey: familyNotesKey(studentId, sessionId),
    queryFn: ({ signal }) => fetchFamilyNotes(studentId, sessionId, signal),
    retry: false,
  });
  if (query.isError) {
    return (
      <FamilyNotesList
        timeZone={timeZone}
        error={query.error}
        onRetry={() => void query.refetch()}
      />
    );
  }
  return query.data ? (
    <FamilyNotesList timeZone={timeZone} notes={query.data.items} />
  ) : null;
}

export function FamilyNotesList({
  notes = [],
  timeZone,
  error,
  onRetry,
}: {
  notes?: readonly FamilyLearningNote[];
  timeZone: string;
  error?: unknown;
  onRetry?: () => void;
}) {
  const { t, i18n } = useTranslation();
  const id = useId();
  const language = toLanguage(i18n.resolvedLanguage);
  if (!error && notes.length === 0) {
    return null;
  }
  return (
    <section
      aria-labelledby={`${id}-title`}
      className="flex min-w-0 flex-col gap-2"
    >
      <h3 id={`${id}-title`} className="text-base font-semibold">
        {t('notes.family.title')}
      </h3>
      {error ? (
        <ErrorAlert error={error} onRetry={onRetry} />
      ) : (
        <ul className="flex min-w-0 flex-col gap-2">
          {notes.map((note) => (
            <li
              key={note.id}
              className="flex min-w-0 flex-col gap-1 rounded-lg border border-border p-3"
            >
              <p className="text-sm break-words whitespace-pre-wrap">
                {note.body}
              </p>
              <p className="text-sm text-muted-foreground">
                {t('notes.family.byline', {
                  name: note.author.display_name,
                  date: formatInstant(note.published_at, {
                    timeZone,
                    language,
                    style: 'date',
                  }),
                })}
              </p>
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}
