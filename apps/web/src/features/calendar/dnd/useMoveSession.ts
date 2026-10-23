import { useQueryClient } from '@tanstack/react-query';
import { useCallback, useRef, useState } from 'react';
import type { SchedulingConflict } from '@/features/scheduling/create/api';
import { conflictsOf } from '@/features/scheduling/create/api';
import {
  CALENDAR_KEY,
  type CalendarItem,
  type CalendarLoaders,
  type CalendarPage,
  type ClassSessionUpdate,
  type ClassSessionUpdated,
} from '../api';
import {
  moveBody,
  refusalKind,
  type MovedSlot,
  type RefusalKind,
} from './rules';

// One move at a time per session (#58 D6), for the drop, the resize and the
// Reschedule dialog alike:
// - the event shows its new slot at once (`positions`, applied over the
//   loaded items) with a pending state, and can't be dragged again until the
//   answer (a second drop is reverted without a request, so a stale version
//   is never sent);
// - 200: the cached range is updated from the response (times, version,
//   state), then the calendar query family is invalidated (markers, other
//   views), and `notice` announces the move;
// - a refusal: the position is dropped (the event is back where the server
//   has it), the drop's `revert()` runs, and `refuse` turns the error into
//   the "Move not saved" dialog (409 scheduling_conflict) or a notice
//   (version conflict with Reload, not editable / not found with a refetch,
//   a 422 field message, anything else in the ErrorAlert). Never a retry.

export type MoveNotice =
  | {
      kind: 'moved';
      session: ClassSessionUpdated;
      /** A series occurrence: it is now an exception (#58 E3). */
      occurrence: boolean;
      overridden: boolean;
    }
  | {
      kind: 'failed';
      error: unknown;
      refusal: Exclude<RefusalKind, 'conflict'>;
      /** The notice takes the focus (a refusal from a dialog). */
      focus: boolean;
    }
  | { kind: 'reloaded' };

/** A move refused with a 409 scheduling_conflict: the attempted body. */
export interface RefusedMove {
  item: CalendarItem;
  body: ClassSessionUpdate;
  conflicts: SchedulingConflict[];
}

/** A second move of a session whose first move is still pending. */
export class MovePendingError extends Error {
  constructor() {
    super('A move of this session is still being saved.');
    this.name = 'MovePendingError';
  }
}

export type UpdateSession = NonNullable<CalendarLoaders['updateSession']>;

function withoutKey<T>(
  record: Record<string, T>,
  key: string,
): Record<string, T> {
  const rest = { ...record };
  delete rest[key];
  return rest;
}

export function useMoveSession(update: UpdateSession | undefined) {
  const client = useQueryClient();
  const [positions, setPositions] = useState<Record<string, MovedSlot>>({});
  const pending = useRef(new Set<string>());
  const [notice, setNotice] = useState<MoveNotice | null>(null);
  const [refused, setRefused] = useState<RefusedMove | null>(null);

  /** Sends one move; resolves with the moved session, rejects with the error. */
  const send = useCallback(
    async (
      item: CalendarItem,
      body: ClassSessionUpdate,
    ): Promise<ClassSessionUpdated> => {
      if (!update || pending.current.has(item.id)) {
        throw new MovePendingError();
      }
      pending.current.add(item.id);
      setNotice(null);
      setPositions((current) => ({
        ...current,
        [item.id]: { starts_at: body.starts_at, ends_at: body.ends_at },
      }));
      try {
        const session = await update(item.id, body);
        client.setQueriesData<CalendarPage>(
          { queryKey: [...CALENDAR_KEY, 'sessions'] },
          (page) =>
            page && {
              ...page,
              items: page.items.map((current) =>
                current.id === session.id
                  ? {
                      ...current,
                      starts_at: session.starts_at,
                      ends_at: session.ends_at,
                      version: session.version,
                      state: session.state,
                    }
                  : current,
              ),
            },
        );
        setNotice({
          kind: 'moved',
          session,
          occurrence: session.series_id !== null,
          overridden: Boolean(body.override),
        });
        void client.invalidateQueries({ queryKey: CALENDAR_KEY });
        return session;
      } finally {
        pending.current.delete(item.id);
        setPositions((current) => withoutKey(current, item.id));
      }
    },
    [client, update],
  );

  /** What a refused move shows (the dialog for a conflict, else a notice). */
  const refuse = useCallback(
    (
      item: CalendarItem,
      body: ClassSessionUpdate,
      error: unknown,
      { focus }: { focus: boolean },
    ) => {
      const kind = refusalKind(error);
      if (kind === 'conflict') {
        setRefused({ item, body, conflicts: conflictsOf(error) ?? [] });
        return;
      }
      setNotice({ kind: 'failed', error, refusal: kind, focus });
      if (kind === 'gone') {
        void client.invalidateQueries({ queryKey: CALENDAR_KEY });
      }
    },
    [client],
  );

  /**
   * A drop or a resize: the new slot, and FullCalendar's `revert` for a
   * refusal (or a second drop while the first is pending).
   */
  const drop = useCallback(
    async (item: CalendarItem, slot: MovedSlot, revert: () => void) => {
      const body = moveBody(item, slot);
      if (!body) {
        return;
      }
      if (pending.current.has(item.id)) {
        revert();
        return;
      }
      try {
        await send(item, body);
      } catch (error) {
        revert();
        refuse(item, body, error, { focus: false });
      }
    },
    [send, refuse],
  );

  /** "Reload" after a version conflict: the range again, then a notice. */
  const reload = useCallback(async () => {
    await client.invalidateQueries({ queryKey: CALENDAR_KEY });
    setNotice({ kind: 'reloaded' });
  }, [client]);

  return {
    positions,
    isPending: (id: string) => id in positions,
    send,
    refuse,
    drop,
    reload,
    notice,
    dismissNotice: () => setNotice(null),
    refused,
    setRefused,
  };
}
