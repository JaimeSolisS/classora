import { useEffect, useEffectEvent, useRef, useState } from 'react';
import { useSearchParams } from 'react-router';
import { useCenterTimeZone } from '@/features/auth/useCenterTimeZone';
import { todayInZone } from '@/lib/time';
import type { ReportBasis } from './api';
import {
  isLocalDate,
  presetRange,
  rangeError,
  reportParams,
  type DateRange,
  type Preset,
  type RangeError,
  type RangeField,
  type ReportParams,
} from './rules';

// The URL-synced basis and range shared by the report pages (M7-14b E2,
// M8-09b B1). A preset or the basis switch writes at once; a typed date
// waits 400 ms, and the debounced write reads the current params when it
// fires (useEffectEvent, BRIEF 2026-11-01), so it keeps a basis switched
// meanwhile. "Today" is the center's: the session's zone until an API
// response gives one (`latchToday`), which re-anchors the default range and
// the presets.

const DATE_DEBOUNCE_MS = 400;

export interface ReportRange {
  /** The center's today (the API's once latched). */
  today: string;
  /** The URL's basis and range (the default preset when absent). */
  params: ReportParams;
  /** The dates as typed. */
  draft: DateRange;
  draftError: RangeError | null;
  urlError: RangeError | null;
  /** Both the typed and the URL range are valid: ask the API. */
  asked: boolean;
  onDate: (field: RangeField, value: string) => void;
  onBasis: (basis: ReportBasis) => void;
  onPreset: (preset: Preset) => void;
  /** Call during render with a response's `today` (null: none yet). */
  latchToday: (reported: string | null | undefined) => void;
}

export function useReportRange(
  defaultPreset: Preset = 'thisMonth',
): ReportRange {
  const timeZone = useCenterTimeZone();
  const [searchParams, setSearchParams] = useSearchParams();

  const [apiToday, setApiToday] = useState<string | null>(null);
  const today = apiToday ?? todayInZone(timeZone);
  const params = reportParams(searchParams, today, defaultPreset);

  // The dates as typed; they follow the URL whenever its range changes.
  const urlRange = `${params.from}/${params.to}`;
  const [draft, setDraft] = useState<DateRange>({
    from: params.from,
    to: params.to,
  });
  const [seenRange, setSeenRange] = useState(urlRange);
  if (seenRange !== urlRange) {
    setSeenRange(urlRange);
    setDraft({ from: params.from, to: params.to });
  }

  const [dateWait, setDateWait] = useState<number | null>(null);
  const keystrokes = useRef(0);

  const writeDates = useEffectEvent(() => {
    setDateWait(null);
    if (!isLocalDate(draft.from) || !isLocalDate(draft.to)) return;
    const next = new URLSearchParams(searchParams);
    next.set('from', draft.from);
    next.set('to', draft.to);
    if (next.toString() !== searchParams.toString()) {
      setSearchParams(next, { replace: true });
    }
  });

  useEffect(() => {
    if (dateWait === null) return;
    const timer = window.setTimeout(() => writeDates(), DATE_DEBOUNCE_MS);
    return () => window.clearTimeout(timer);
  }, [dateWait]);

  function onDate(field: RangeField, value: string) {
    setDraft((current) => ({ ...current, [field]: value }));
    keystrokes.current += 1;
    setDateWait(keystrokes.current);
  }

  function onBasis(basis: ReportBasis) {
    const next = new URLSearchParams(searchParams);
    next.set('basis', basis);
    setSearchParams(next);
  }

  function onPreset(preset: Preset) {
    setDateWait(null);
    const range = presetRange(preset, today);
    setDraft(range);
    const next = new URLSearchParams(searchParams);
    next.set('from', range.from);
    next.set('to', range.to);
    setSearchParams(next);
  }

  function latchToday(reported: string | null | undefined) {
    if (reported && reported !== apiToday) {
      setApiToday(reported);
    }
  }

  const draftError = rangeError(draft, today);
  const urlError = rangeError(params, today);
  return {
    today,
    params,
    draft,
    draftError,
    urlError,
    asked: draftError === null && urlError === null,
    onDate,
    onBasis,
    onPreset,
    latchToday,
  };
}
