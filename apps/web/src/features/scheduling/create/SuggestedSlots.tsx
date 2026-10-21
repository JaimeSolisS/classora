import { useId } from 'react';
import { useTranslation } from 'react-i18next';
import { ErrorAlert } from '@/components/feedback/ErrorAlert';
import { Spinner } from '@/components/feedback/Spinner';
import { useFormatters } from '@/lib/formatters';
import type { SlotSuggestions, SuggestedSlot } from './api';
import { useSlotParts } from './slotParts';
import { useWarningMessage } from './warningMessages';

// "Suggested times" (#50 C1), rendered under the conflict panel after a 409:
// the free times the server found for the same session (nearest first),
// each a button with its date and times in the center zone, plus a
// "Workload warning" note when a create at that time would warn. Choosing
// one hands it to the form, which fills in the fields (it never submits).
// Loading, empty and error states; the error is the shared ErrorAlert.

export type SuggestionsState =
  | { status: 'loading' }
  | { status: 'loaded'; result: SlotSuggestions }
  | { status: 'error'; error: unknown };

export interface SuggestedSlotsProps {
  state: SuggestionsState;
  timeZone: string;
  onChoose: (slot: SuggestedSlot) => void;
}

export function SuggestedSlots({
  state,
  timeZone,
  onChoose,
}: SuggestedSlotsProps) {
  const { t } = useTranslation();
  const { formatLocalDate } = useFormatters();
  const warningMessage = useWarningMessage();
  const slotParts = useSlotParts(timeZone);
  const id = useId();
  const headingId = `${id}-heading`;
  const zoneId = `${id}-zone`;

  let content;
  if (state.status === 'loading') {
    content = (
      <p role="status" className="flex items-center gap-2 text-sm">
        <Spinner />
        {t('scheduling.suggestions.loading')}
      </p>
    );
  } else if (state.status === 'error') {
    content = <ErrorAlert error={state.error} />;
  } else if (state.result.slots.length === 0) {
    content = (
      <p role="status" className="text-sm">
        {t('scheduling.suggestions.empty', {
          from: formatLocalDate(state.result.search_from, 'long'),
          to: formatLocalDate(state.result.search_to, 'long'),
        })}
      </p>
    );
  } else {
    content = (
      <>
        <p id={zoneId} className="text-sm text-muted-foreground">
          {t('scheduling.zone', { zone: timeZone })}
        </p>
        <ul className="flex flex-col gap-2">
          {state.result.slots.map((slot, index) => {
            const noteId = `${id}-note-${index}`;
            const hasWarnings = slot.warnings.length > 0;
            return (
              <li key={slot.starts_at} className="flex flex-col gap-1">
                <button
                  type="button"
                  aria-describedby={
                    hasWarnings ? `${zoneId} ${noteId}` : zoneId
                  }
                  onClick={() => onChoose(slot)}
                  className="w-full rounded-md border border-border px-3 py-2 text-left text-sm font-medium break-words hover:bg-muted focus-visible:ring-3 focus-visible:ring-ring focus-visible:outline-none"
                >
                  {t('scheduling.suggestions.slot', slotParts(slot))}
                </button>
                {hasWarnings ? (
                  <div id={noteId} className="pl-3 text-sm">
                    <p className="font-medium">
                      {t('scheduling.warnings.note')}
                    </p>
                    <ul className="list-disc pl-5 break-words">
                      {slot.warnings.map((warning, position) => (
                        <li key={`${warning.code}-${position}`}>
                          {warningMessage(warning)}
                        </li>
                      ))}
                    </ul>
                  </div>
                ) : null}
              </li>
            );
          })}
        </ul>
      </>
    );
  }

  return (
    <section
      aria-labelledby={headingId}
      className="flex flex-col gap-3 rounded-md border border-border p-4"
    >
      <h2 id={headingId} className="font-medium">
        {t('scheduling.suggestions.heading')}
      </h2>
      <p className="text-sm text-muted-foreground">
        {t('scheduling.suggestions.intro')}
      </p>
      {content}
    </section>
  );
}
