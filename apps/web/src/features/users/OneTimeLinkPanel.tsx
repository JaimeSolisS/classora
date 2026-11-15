import { useEffect, useId, useRef, useState } from 'react';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';

// Shows a one-time link once, right after the API returned it (#29 I3, and
// #30 for reset links): who it is for, the link in a read-only field with a
// visible label, "Copy link", a "shown only once" warning and "Done". There
// is only the link (ADR-0007 D11 as amended by #29). Presentational: the page
// passes every text, so another link kind reuses it.
//
// The link lives only in the caller's component state (passed in as `url`):
// never a store, browser storage, the query cache, the URL or a log. "Done"
// tells the caller to drop it.

export interface OneTimeLinkDetail {
  label: string;
  value: string;
}

export interface OneTimeLinkLabels {
  /** The panel heading. */
  title: string;
  /** The visible label of the link field. */
  link: string;
  /** The "shown only once" warning. */
  warning: string;
  copy: string;
  /** Announced after a successful copy. */
  copied: string;
  /** Announced when the copy failed (the link is then selected). */
  copyFailed: string;
  done: string;
}

interface OneTimeLinkPanelProps {
  url: string;
  /** Who the link is for and when it expires (already formatted). */
  details: readonly OneTimeLinkDetail[];
  labels: OneTimeLinkLabels;
  onDone: () => void;
}

type CopyState = 'idle' | 'copied' | 'failed';

export function OneTimeLinkPanel({
  url,
  details,
  labels,
  onDone,
}: OneTimeLinkPanelProps) {
  const id = useId();
  const linkId = `${id}-link`;
  const warningId = `${id}-warning`;
  const headingRef = useRef<HTMLHeadingElement>(null);
  const linkRef = useRef<HTMLInputElement>(null);
  const [copy, setCopy] = useState<CopyState>('idle');

  // The panel takes focus when it appears, so keyboard and screen-reader
  // users land on it. The caller keys the panel by link, so a new link is a
  // new panel (and a fresh copy state).
  useEffect(() => {
    headingRef.current?.focus();
  }, []);

  async function copyLink() {
    try {
      if (!navigator.clipboard) {
        throw new Error('no clipboard');
      }
      await navigator.clipboard.writeText(url);
      setCopy('copied');
    } catch {
      // Selected, so the person can copy it by hand.
      linkRef.current?.focus();
      linkRef.current?.select();
      setCopy('failed');
    }
  }

  return (
    <section
      aria-labelledby={`${id}-title`}
      className="flex flex-col gap-4 rounded-md border border-border p-4"
    >
      <h2
        id={`${id}-title`}
        ref={headingRef}
        tabIndex={-1}
        className="text-lg font-semibold outline-none"
      >
        {labels.title}
      </h2>
      <dl className="grid grid-cols-[auto_1fr] gap-x-4 gap-y-1 text-sm">
        {details.map((detail) => (
          <div key={detail.label} className="contents">
            <dt className="font-medium">{detail.label}</dt>
            <dd className="min-w-0 break-words">{detail.value}</dd>
          </div>
        ))}
      </dl>
      <div className="flex flex-col gap-2">
        <label htmlFor={linkId} className="text-sm font-medium">
          {labels.link}
        </label>
        <Input
          id={linkId}
          ref={linkRef}
          type="text"
          readOnly
          value={url}
          spellCheck={false}
          aria-describedby={warningId}
          onFocus={(event) => event.currentTarget.select()}
        />
      </div>
      <p
        id={warningId}
        className="rounded-md border border-border bg-muted p-3 text-sm"
      >
        {labels.warning}
      </p>
      <div className="flex flex-wrap items-center gap-3">
        <Button type="button" onClick={() => void copyLink()}>
          {labels.copy}
        </Button>
        <Button type="button" variant="outline" onClick={onDone}>
          {labels.done}
        </Button>
        <p role="status" className="text-sm">
          {copy === 'copied'
            ? labels.copied
            : copy === 'failed'
              ? labels.copyFailed
              : null}
        </p>
      </div>
    </section>
  );
}
