import {
  closestCenter,
  DndContext,
  KeyboardSensor,
  PointerSensor,
  TouchSensor,
  useSensor,
  useSensors,
  type Announcements,
  type DragEndEvent,
  type ScreenReaderInstructions,
  type UniqueIdentifier,
} from '@dnd-kit/core';
import {
  arrayMove,
  SortableContext,
  sortableKeyboardCoordinates,
  useSortable,
  verticalListSortingStrategy,
} from '@dnd-kit/sortable';
import { CSS } from '@dnd-kit/utilities';
import { ChevronDown, ChevronUp, GripVertical } from 'lucide-react';
import { useId, useLayoutEffect, useRef, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { ErrorAlert } from '@/components/feedback/ErrorAlert';
import { Spinner } from '@/components/feedback/Spinner';
import { Button } from '@/components/ui/button';
import { Checkbox } from '@/components/ui/checkbox';
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog';
import { useReturnFocus } from '@/features/scheduling/series/hooks';
import { cn } from '@/lib/utils';
import { isWidgetUnavailable, type LayoutEntry, type WidgetCode } from './api';

// The "Customize" dialog of the dashboard (M8-10 E1–E3, #127 E1, E2): every
// widget of the effective layout in order, each with a drag handle (dnd-kit
// sortable: pointer, touch and keyboard, with localized instructions and
// announcements), a "Show" checkbox and "Move up" / "Move down" buttons
// (the single-pointer alternative to dragging, WCAG 2.2 SC 2.5.7). Save
// sends the whole list in this order; Cancel (and Esc) sends nothing;
// "Restore default" deletes the saved layout. Its own chunk: dnd-kit is
// loaded only when the dialog first opens.

export interface DialogEntry extends LayoutEntry {
  title: string;
}

interface CustomizeDialogProps {
  open: boolean;
  entries: readonly DialogEntry[];
  onClose: () => void;
  /** PUT the layout; the caller updates its query. */
  onSave: (widgets: readonly LayoutEntry[]) => Promise<void>;
  /** DELETE the layout, then refetch it. */
  onReset: () => Promise<void>;
  /** After a 422 dashboard_widget_unavailable: the refetched layout. */
  onUnavailable: () => Promise<DialogEntry[]>;
}

export function CustomizeDialog(props: CustomizeDialogProps) {
  const busy = useRef(false);
  const dragging = useRef(false);
  return (
    <Dialog
      open={props.open}
      onOpenChange={(next) => {
        if (!next && !busy.current) {
          props.onClose();
        }
      }}
    >
      {props.open ? (
        <DialogBody {...props} busyRef={busy} draggingRef={dragging} />
      ) : null}
    </Dialog>
  );
}

function prefersReducedMotion(): boolean {
  return (
    typeof window.matchMedia === 'function' &&
    window.matchMedia('(prefers-reduced-motion: reduce)').matches
  );
}

type Direction = 'up' | 'down';

function DialogBody({
  entries,
  onClose,
  onSave,
  onReset,
  onUnavailable,
  busyRef,
  draggingRef,
}: CustomizeDialogProps & {
  busyRef: { current: boolean };
  draggingRef: { current: boolean };
}) {
  const { t } = useTranslation();
  const returnFocus = useReturnFocus();
  const listId = useId();
  const [items, setItems] = useState<DialogEntry[]>(() => [...entries]);
  const [moved, setMoved] = useState('');
  const [notice, setNotice] = useState(false);
  const [failure, setFailure] = useState<unknown>(null);
  const [submitting, setSubmitting] = useState<'save' | 'reset' | null>(null);
  // The move whose button gets the focus back once the list re-renders.
  const focusAfterMove = useRef<{
    widget: WidgetCode;
    direction: Direction;
  } | null>(null);
  // dnd-kit's live region and instructions go inside the dialog, which
  // hides everything outside it from assistive technology.
  const [liveContainer, setLiveContainer] = useState<HTMLDivElement | null>(
    null,
  );
  const reduced = prefersReducedMotion();
  const total = items.length;

  const sensors = useSensors(
    useSensor(PointerSensor),
    useSensor(TouchSensor, {
      activationConstraint: { delay: 200, tolerance: 8 },
    }),
    useSensor(KeyboardSensor, {
      coordinateGetter: sortableKeyboardCoordinates,
    }),
  );

  const titleOf = (id: UniqueIdentifier | undefined) =>
    items.find((item) => item.widget === id)?.title ?? '';
  const positionOf = (id: UniqueIdentifier | undefined) =>
    items.findIndex((item) => item.widget === id) + 1;
  const L = 'dashboard.dialog.announcements';
  const justLifted = useRef(false);
  const announcements: Announcements = {
    onDragStart: ({ active }) => {
      justLifted.current = true;
      return t(`${L}.start`, {
        title: titleOf(active.id),
        position: positionOf(active.id),
        total,
      });
    },
    // Right after the lift dnd-kit reports the item over itself: keep the
    // start message (later moves back to the start are still announced).
    onDragOver: ({ active, over }) => {
      const first = justLifted.current;
      justLifted.current = false;
      if (first && over?.id === active.id) return undefined;
      return over
        ? t(`${L}.over`, {
            title: titleOf(active.id),
            position: positionOf(over.id),
            total,
          })
        : t(`${L}.outside`, { title: titleOf(active.id) });
    },
    onDragEnd: ({ active, over }) =>
      over
        ? t(`${L}.end`, {
            title: titleOf(active.id),
            position: positionOf(over.id),
            total,
          })
        : t(`${L}.cancel`, {
            title: titleOf(active.id),
            position: positionOf(active.id),
            total,
          }),
    onDragCancel: ({ active }) =>
      t(`${L}.cancel`, {
        title: titleOf(active.id),
        position: positionOf(active.id),
        total,
      }),
  };
  const screenReaderInstructions: ScreenReaderInstructions = {
    draggable: t('dashboard.dialog.instructions'),
  };

  function endDrag() {
    // Esc ends a keyboard drag; let that keystroke pass before the dialog
    // may close on Esc again.
    window.setTimeout(() => {
      draggingRef.current = false;
    }, 0);
  }

  function onDragEnd({ active, over }: DragEndEvent) {
    endDrag();
    if (!over || active.id === over.id) return;
    setItems((current) => {
      const from = current.findIndex((item) => item.widget === active.id);
      const to = current.findIndex((item) => item.widget === over.id);
      return from < 0 || to < 0 ? current : arrayMove(current, from, to);
    });
  }

  function move(index: number, direction: Direction) {
    const target = direction === 'up' ? index - 1 : index + 1;
    if (target < 0 || target >= items.length) return;
    const item = items[index]!;
    setItems(arrayMove(items, index, target));
    setMoved(
      t('dashboard.dialog.position', {
        title: item.title,
        position: target + 1,
        total,
      }),
    );
    focusAfterMove.current = { widget: item.widget, direction };
  }

  // E1: after a move, focus stays on the moved row's same button (or its
  // other move button when that one is now disabled at an end).
  const listRef = useRef<HTMLOListElement>(null);
  useLayoutEffect(() => {
    const pending = focusAfterMove.current;
    if (!pending) return;
    focusAfterMove.current = null;
    const row = listRef.current?.querySelector(
      `[data-dashboard-row="${pending.widget}"]`,
    );
    const same = row?.querySelector<HTMLButtonElement>(
      `[data-move="${pending.direction}"]`,
    );
    const other = row?.querySelector<HTMLButtonElement>(
      `[data-move="${pending.direction === 'up' ? 'down' : 'up'}"]`,
    );
    (same && !same.disabled ? same : other)?.focus();
  }, [items]);

  function toggle(widget: WidgetCode, visible: boolean) {
    setItems((current) =>
      current.map((item) =>
        item.widget === widget ? { ...item, visible } : item,
      ),
    );
  }

  async function run(which: 'save' | 'reset') {
    if (busyRef.current) return;
    busyRef.current = true;
    setSubmitting(which);
    setFailure(null);
    setNotice(false);
    try {
      if (which === 'save') {
        await onSave(items.map(({ widget, visible }) => ({ widget, visible })));
      } else {
        await onReset();
      }
      busyRef.current = false;
      onClose();
      return;
    } catch (error) {
      if (which === 'save' && isWidgetUnavailable(error)) {
        try {
          setItems(await onUnavailable());
          setNotice(true);
        } catch (refetchError) {
          setFailure(refetchError);
        }
      } else {
        setFailure(error);
      }
    }
    busyRef.current = false;
    setSubmitting(null);
  }

  return (
    <DialogContent
      className="max-h-[calc(100dvh-2rem)] overflow-y-auto sm:max-w-xl"
      onOpenAutoFocus={returnFocus.onOpenAutoFocus}
      onCloseAutoFocus={returnFocus.onCloseAutoFocus}
      onInteractOutside={(event) => event.preventDefault()}
      onEscapeKeyDown={(event) => {
        if (draggingRef.current) event.preventDefault();
      }}
      data-dashboard-dialog
    >
      <DialogHeader>
        <DialogTitle>{t('dashboard.dialog.title')}</DialogTitle>
        <DialogDescription>
          {t('dashboard.dialog.description')}
        </DialogDescription>
      </DialogHeader>
      <div ref={setLiveContainer} />
      {notice ? (
        <p role="alert" className="text-sm font-medium text-destructive">
          {t('dashboard.dialog.permissionsChanged')}
        </p>
      ) : null}
      {failure ? <ErrorAlert error={failure} /> : null}
      <p className="sr-only" id={listId}>
        {t('dashboard.dialog.listLabel')}
      </p>
      {liveContainer ? (
        <DndContext
          sensors={sensors}
          collisionDetection={closestCenter}
          accessibility={{
            announcements,
            screenReaderInstructions,
            container: liveContainer,
          }}
          onDragStart={() => {
            draggingRef.current = true;
          }}
          onDragEnd={onDragEnd}
          onDragCancel={endDrag}
        >
          <SortableContext
            items={items.map((item) => item.widget)}
            strategy={verticalListSortingStrategy}
          >
            <ol
              ref={listRef}
              aria-labelledby={listId}
              className="flex flex-col gap-2"
              data-dashboard-rows
            >
              {items.map((item, index) => (
                <SortableRow
                  key={item.widget}
                  item={item}
                  first={index === 0}
                  last={index === items.length - 1}
                  reduced={reduced}
                  onToggle={(visible) => toggle(item.widget, visible)}
                  onMove={(direction) => move(index, direction)}
                />
              ))}
            </ol>
          </SortableContext>
        </DndContext>
      ) : null}
      <p aria-live="polite" className="sr-only" data-dashboard-moved>
        {moved}
      </p>
      <DialogFooter className="flex-col-reverse gap-2 sm:flex-row sm:justify-between">
        <Button
          type="button"
          variant="ghost"
          className="min-h-11 md:min-h-9"
          disabled={submitting !== null}
          onClick={() => void run('reset')}
        >
          {submitting === 'reset' ? <Spinner /> : null}
          {t('dashboard.dialog.reset')}
        </Button>
        <div className="flex flex-col-reverse gap-2 sm:flex-row">
          <Button
            type="button"
            variant="outline"
            className="min-h-11 md:min-h-9"
            disabled={submitting !== null}
            onClick={onClose}
          >
            {t('dashboard.dialog.cancel')}
          </Button>
          <Button
            type="button"
            className="min-h-11 md:min-h-9"
            disabled={submitting !== null}
            onClick={() => void run('save')}
          >
            {submitting === 'save' ? <Spinner /> : null}
            {t('dashboard.dialog.save')}
          </Button>
        </div>
      </DialogFooter>
    </DialogContent>
  );
}

const ICON_BUTTON =
  'inline-flex size-11 shrink-0 items-center justify-center rounded-md border border-input bg-background text-foreground outline-none hover:bg-accent focus-visible:ring-3 focus-visible:ring-ring disabled:cursor-not-allowed disabled:opacity-50';

function SortableRow({
  item,
  first,
  last,
  reduced,
  onToggle,
  onMove,
}: {
  item: DialogEntry;
  first: boolean;
  last: boolean;
  reduced: boolean;
  onToggle: (visible: boolean) => void;
  onMove: (direction: Direction) => void;
}) {
  const { t } = useTranslation();
  const checkboxId = useId();
  const {
    attributes,
    listeners,
    setNodeRef,
    setActivatorNodeRef,
    transform,
    transition,
    isDragging,
  } = useSortable({
    id: item.widget,
    ...(reduced ? { transition: null } : {}),
  });
  const style = {
    transform: CSS.Translate.toString(transform),
    transition: reduced ? undefined : transition,
  };
  const title = item.title;
  return (
    <li
      ref={setNodeRef}
      style={style}
      className={cn(
        // E3: below ~375 px the move buttons wrap, together, to a second
        // line, so a row fits the dialog down to 320 px.
        'flex min-w-0 flex-wrap items-center gap-2 rounded-md border border-border bg-card p-2',
        isDragging && 'relative z-10 shadow-md',
      )}
      data-dashboard-row={item.widget}
    >
      <button
        type="button"
        ref={setActivatorNodeRef}
        {...attributes}
        {...listeners}
        aria-label={t('dashboard.dialog.reorder', { title })}
        className={cn(ICON_BUTTON, 'cursor-grab touch-none border-transparent')}
        data-drag-handle
      >
        <GripVertical aria-hidden="true" className="size-5" />
      </button>
      <div className="flex min-w-28 flex-1 basis-0 items-center gap-3">
        <Checkbox
          id={checkboxId}
          checked={item.visible}
          aria-label={t('dashboard.dialog.show', { title })}
          onCheckedChange={(checked) => onToggle(checked === true)}
        />
        <label
          htmlFor={checkboxId}
          className="min-w-0 py-2 text-sm font-medium wrap-anywhere"
        >
          {title}
        </label>
      </div>
      <div className="ml-auto flex shrink-0 gap-2" data-move-buttons>
        <button
          type="button"
          className={ICON_BUTTON}
          aria-label={t('dashboard.dialog.moveUp', { title })}
          disabled={first}
          onClick={() => onMove('up')}
          data-move="up"
        >
          <ChevronUp aria-hidden="true" className="size-5" />
        </button>
        <button
          type="button"
          className={ICON_BUTTON}
          aria-label={t('dashboard.dialog.moveDown', { title })}
          disabled={last}
          onClick={() => onMove('down')}
          data-move="down"
        >
          <ChevronDown aria-hidden="true" className="size-5" />
        </button>
      </div>
    </li>
  );
}
