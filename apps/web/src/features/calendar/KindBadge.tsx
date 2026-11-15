import { useId } from 'react';
import { useTranslation } from 'react-i18next';
import { cn } from '@/lib/utils';
import {
  KIND_ICONS,
  kindStrong,
  kindTint,
  SESSION_KINDS,
  type SessionKind,
} from './kinds';

// The class type of a session (HF-07 E1): a colour (tokens.css: the
// `--kind-<kind>` tint behind calendar events and the `--kind-<kind>-strong`
// shade for swatches and List dots) that never stands alone. An icon, the
// type's text (or its tooltip on short events), the accessible name and the
// legend carry it too. Shared by both calendars, their legend and Home.
// The codes, icons and colour values are in kinds.ts.

/** A small square in the type's colours: the event tint, outlined with the
 * strong shade (3:1 against the page and the card). Decorative. */
export function KindSwatch({ kind }: { kind: SessionKind }) {
  return (
    <span
      aria-hidden="true"
      className="inline-block size-3 shrink-0 rounded-sm border-2"
      style={{ backgroundColor: kindTint(kind), borderColor: kindStrong(kind) }}
      data-kind-swatch={kind}
    />
  );
}

/**
 * The type as its icon and text, optionally after a swatch. `iconOnly`
 * (short calendar events) keeps the text as the tooltip; the event's
 * accessible name carries it.
 */
export function KindBadge({
  kind,
  iconOnly = false,
  swatch = false,
  className,
}: {
  kind: SessionKind;
  iconOnly?: boolean;
  swatch?: boolean;
  className?: string;
}) {
  const { t } = useTranslation();
  const Icon = KIND_ICONS[kind];
  const label = t(`scheduling.calendar.filters.kinds.${kind}`);
  return (
    <span
      className={cn('inline-flex items-center gap-1', className)}
      title={iconOnly ? label : undefined}
      data-kind={kind}
    >
      {swatch ? <KindSwatch kind={kind} /> : null}
      <Icon aria-hidden="true" className="size-3.5 shrink-0" />
      {iconOnly ? null : label}
    </span>
  );
}

/** "Class types: ■ Individual ■ Group ■ Course", above the calendars. Not
 * interactive: the class type filter stays the way to filter. */
export function KindLegend() {
  const { t } = useTranslation();
  const id = useId();
  return (
    <div
      className="flex flex-wrap items-center gap-x-3 gap-y-1 text-sm"
      data-kind-legend
    >
      <span id={id} className="text-muted-foreground">
        {t('scheduling.calendar.legend.title')}
      </span>
      <ul aria-labelledby={id} className="flex flex-wrap gap-x-3 gap-y-1">
        {SESSION_KINDS.map((kind) => (
          <li key={kind}>
            <KindBadge kind={kind} swatch />
          </li>
        ))}
      </ul>
    </div>
  );
}
