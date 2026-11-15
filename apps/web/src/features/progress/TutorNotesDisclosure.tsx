import { useQuery } from '@tanstack/react-query';
import { ChevronDown, ChevronRight } from 'lucide-react';
import { useId, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { ErrorAlert } from '@/components/feedback/ErrorAlert';
import { LoadingState } from '@/components/feedback/LoadingState';
import { useFormatters } from '@/lib/formatters';
import {
  familyNotesKey,
  fetchFamilyNotes,
} from '@/features/tutor-portal/notes/api';

// "Notes from the tutor ({n})" in a family's class log item (#322 C5): a
// disclosure button (aria-expanded, aria-controls) that loads #79's family
// route (`learning-notes?class_session_id=`, shared notes only) the first
// time it opens, then lists each note's text (plain text), the tutor's name
// and the date it was shared. A failure shows an inline error with "Try
// again". Staff and tutors never get this (they see the count only).

export function TutorNotesDisclosure({
  studentId,
  sessionId,
  count,
  initiallyOpen = false,
}: {
  studentId: string;
  sessionId: string;
  count: number;
  /** Stories. */
  initiallyOpen?: boolean;
}) {
  const { t } = useTranslation();
  const { formatInstant } = useFormatters();
  const id = useId();
  const [open, setOpen] = useState(initiallyOpen);
  const notes = useQuery({
    queryKey: familyNotesKey(studentId, sessionId),
    queryFn: ({ signal }) => fetchFamilyNotes(studentId, sessionId, signal),
    enabled: open,
    retry: false,
    // Closing and reopening shows the notes already loaded.
    staleTime: 5 * 60_000,
  });
  const Icon = open ? ChevronDown : ChevronRight;
  return (
    <div className="flex min-w-0 flex-col gap-2">
      <button
        type="button"
        aria-expanded={open}
        aria-controls={`${id}-notes`}
        onClick={() => setOpen((value) => !value)}
        className="inline-flex min-h-6 items-center gap-1 self-start rounded-sm text-sm font-medium underline-offset-4 outline-none hover:underline focus-visible:ring-3 focus-visible:ring-ring"
      >
        <Icon aria-hidden="true" className="size-4" />
        {t('progress.log.notes', { count })}
      </button>
      <div id={`${id}-notes`} hidden={!open} className="min-w-0">
        {!open ? null : notes.isError ? (
          <ErrorAlert
            error={notes.error}
            onRetry={() => void notes.refetch()}
          />
        ) : !notes.data ? (
          <LoadingState rows={2} />
        ) : (
          <ul className="flex min-w-0 flex-col gap-2">
            {notes.data.items.map((note) => (
              <li
                key={note.id}
                className="flex min-w-0 flex-col gap-1 rounded-md border border-border p-3"
              >
                <p className="text-sm break-words whitespace-pre-line">
                  {note.body}
                </p>
                <p className="text-sm text-muted-foreground">
                  {t('notes.family.byline', {
                    name: note.author.display_name,
                    date: formatInstant(note.published_at, 'date'),
                  })}
                </p>
              </li>
            ))}
          </ul>
        )}
      </div>
    </div>
  );
}
