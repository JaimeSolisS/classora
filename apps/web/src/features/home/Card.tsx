import { useId, type ReactNode } from 'react';

// A home section (HF-06 D4): a token-only card with its own heading. On the
// role home the sections are h2 under the greeting; inside the dashboard's
// "Today" panel (itself an h2) they are h3.

export type HeadingLevel = 2 | 3;

export function HomeCard({
  title,
  level,
  children,
  ...data
}: {
  title: string;
  level: HeadingLevel;
  children: ReactNode;
} & Record<`data-${string}`, string | boolean>) {
  const id = useId();
  const Heading = level === 2 ? 'h2' : 'h3';
  return (
    <section
      aria-labelledby={id}
      className="flex min-w-0 flex-col gap-3 rounded-lg border border-border bg-card p-4 text-card-foreground"
      {...data}
    >
      <Heading id={id} className="text-lg font-semibold">
        {title}
      </Heading>
      {children}
    </section>
  );
}

/** A secondary text line (empty states, "and n more"). */
export const NOTE_CLASS = 'text-sm text-muted-foreground';

/** A text link that is a 24 px target (44 px rows below md, D4). */
export const LINK_CLASS =
  'inline-flex min-h-11 items-center self-start text-sm font-medium underline underline-offset-4 outline-none focus-visible:ring-3 focus-visible:ring-ring md:min-h-6';

/** A list row link: the whole row is the target. */
export const ROW_LINK_CLASS =
  'flex min-h-11 min-w-0 flex-col gap-0.5 rounded-md px-2 py-1.5 outline-none hover:bg-muted focus-visible:ring-3 focus-visible:ring-ring md:min-h-6';
