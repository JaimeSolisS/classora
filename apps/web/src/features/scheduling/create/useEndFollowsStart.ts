import { useRef, useState } from 'react';
import { addMinutes } from '@/lib/timeOfDay';
import {
  DEFAULT_DURATION_MINUTES,
  endForStart,
  READABLE_TIMES,
  type UnreadableTimes,
} from './rules';

// A start and an end typed or picked in two time fields (HF-13, #598 E3 and
// QA D1; shared by #600). A new start moves the end: every keystroke of one
// edit of the start is measured from the start and end as they were when
// that edit began, so in-between values (a cleared field, `93`, `25:00`,
// Escape) never change the result. The edit ends on blur, Tab, Enter,
// Escape or a chosen option (`TimeField`'s `onEditEnd`), and on any change
// to the end. An empty end, or one not after the old start, becomes the new
// start + `fallbackMinutes`: one hour, or the length a dialog opened with
// (#600 E2 a). It also tracks which field holds text that isn't a time.

export interface StartAndEnd {
  start: string;
  end: string;
}

export interface EndFollowsStart {
  /** Which field holds text that isn't a time (its error says so). */
  unreadable: UnreadableTimes;
  /** The start and end after the start changed to `start`. */
  changeStart: (
    current: StartAndEnd,
    start: string,
    isUnreadable: boolean,
  ) => StartAndEnd;
  /** The end changed (it ends any edit of the start). */
  changeEnd: (isUnreadable: boolean) => void;
  /** `changeStart` or `changeEnd`: the start and end after `field` changed. */
  change: (
    current: StartAndEnd,
    field: 'start' | 'end',
    value: string,
    isUnreadable: boolean,
  ) => StartAndEnd;
  /** The edit of the start is over (the start field's `onEditEnd`). */
  endEdit: () => void;
  /** A fresh pair (a chosen suggestion, "Schedule another"). */
  reset: () => void;
  /** Where the end's list opens for `start`: start + the fallback length. */
  endScrollTo: (start: string) => string | undefined;
}

export function useEndFollowsStart(
  fallbackMinutes: number = DEFAULT_DURATION_MINUTES,
): EndFollowsStart {
  // The start and end when the current edit of the start began.
  const edit = useRef<StartAndEnd | null>(null);
  const [unreadable, setUnreadable] = useState<UnreadableTimes>(READABLE_TIMES);

  function changeStart(
    current: StartAndEnd,
    start: string,
    isUnreadable: boolean,
  ): StartAndEnd {
    edit.current ??= { start: current.start, end: current.end };
    const end = endForStart(edit.current, start, fallbackMinutes);
    setUnreadable((was) => ({
      start: isUnreadable,
      // A moved end holds a time again.
      end: end !== current.end ? false : was.end,
    }));
    return { start, end };
  }

  function changeEnd(isUnreadable: boolean) {
    edit.current = null;
    setUnreadable((was) => ({ ...was, end: isUnreadable }));
  }

  function change(
    current: StartAndEnd,
    field: 'start' | 'end',
    value: string,
    isUnreadable: boolean,
  ): StartAndEnd {
    if (field === 'start') {
      return changeStart(current, value, isUnreadable);
    }
    changeEnd(isUnreadable);
    return { start: current.start, end: value };
  }

  function endEdit() {
    edit.current = null;
  }

  function reset() {
    edit.current = null;
    setUnreadable(READABLE_TIMES);
  }

  function endScrollTo(start: string): string | undefined {
    return addMinutes(start, fallbackMinutes) ?? undefined;
  }

  return {
    unreadable,
    changeStart,
    changeEnd,
    change,
    endEdit,
    reset,
    endScrollTo,
  };
}
