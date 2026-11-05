import { useTranslation } from 'react-i18next';
import { MASKED, type AuditAccount, type AuditEvent } from './api';

// One event's before/after view (#120 C5). `changes`: a Field | Before |
// After table, columns sorted; a create leaves Before empty and a delete
// leaves After empty; null shows "—". A masked value ("[masked]") is an
// "Hidden" badge, never a value. A user id the API named (`accounts`) shows
// the name with the id as secondary text. Objects and arrays are wrapped,
// pretty-printed JSON. A named event's `details` are key/value rows in the
// same format, and the request ID is shown when there is one. Read only.

const CELL = 'px-2 py-2 align-top break-words';
const HEAD = 'px-2 py-2 text-left text-xs font-medium text-muted-foreground';

export function AuditValue({
  value,
  accounts,
}: {
  value: unknown;
  accounts: readonly AuditAccount[];
}) {
  const { t } = useTranslation();
  if (value === null || value === undefined) {
    return <span>{t('audit.diff.none')}</span>;
  }
  if (value === MASKED) {
    return (
      <span className="inline-flex items-center rounded-md border border-border bg-muted px-2 py-0.5 text-xs font-medium">
        <span aria-hidden="true">{t('audit.diff.masked')}</span>
        <span className="sr-only">{t('audit.diff.maskedLabel')}</span>
      </span>
    );
  }
  if (typeof value === 'string') {
    const account = accounts.find((entry) => entry.id === value);
    if (account) {
      return (
        <span className="flex min-w-0 flex-col">
          <span className="break-words">{account.display_name}</span>
          <span className="font-mono text-xs break-all text-muted-foreground">
            {value}
          </span>
        </span>
      );
    }
    return <span className="break-words whitespace-pre-wrap">{value}</span>;
  }
  if (typeof value === 'object') {
    return (
      <pre className="font-mono text-xs break-all whitespace-pre-wrap">
        {JSON.stringify(value, null, 2)}
      </pre>
    );
  }
  // A number or a boolean (JSON has nothing else left).
  return <span>{JSON.stringify(value)}</span>;
}

export function AuditChanges({
  event,
  id,
}: {
  event: AuditEvent;
  /** The region's id (the "View changes" button's aria-controls). */
  id: string;
}) {
  const { t } = useTranslation();
  const changes = event.changes
    ? Object.entries(event.changes).sort(([a], [b]) => (a < b ? -1 : 1))
    : [];
  const details = event.details
    ? Object.entries(event.details).sort(([a], [b]) => (a < b ? -1 : 1))
    : [];
  const showOld = event.action !== 'create';
  const showNew = event.action !== 'delete';
  return (
    <div
      id={id}
      role="region"
      aria-label={t('audit.diff.label')}
      className="flex min-w-0 flex-col gap-3 text-sm"
    >
      {changes.length > 0 ? (
        <table className="w-full table-fixed border-collapse">
          <caption className="sr-only">
            {t('audit.diff.changesCaption')}
          </caption>
          <thead>
            <tr className="border-b border-border">
              <th scope="col" className={HEAD}>
                {t('audit.diff.field')}
              </th>
              <th scope="col" className={HEAD}>
                {t('audit.diff.before')}
              </th>
              <th scope="col" className={HEAD}>
                {t('audit.diff.after')}
              </th>
            </tr>
          </thead>
          <tbody>
            {changes.map(([column, change]) => (
              <tr key={column} className="border-b border-border">
                <th
                  scope="row"
                  className={`${CELL} text-left font-mono text-xs font-normal break-all`}
                >
                  {column}
                </th>
                <td className={CELL}>
                  {showOld ? (
                    <AuditValue value={change.old} accounts={event.accounts} />
                  ) : null}
                </td>
                <td className={CELL}>
                  {showNew ? (
                    <AuditValue value={change.new} accounts={event.accounts} />
                  ) : null}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      ) : null}
      {details.length > 0 ? (
        <table className="w-full table-fixed border-collapse">
          <caption className="sr-only">
            {t('audit.diff.detailsCaption')}
          </caption>
          <thead>
            <tr className="border-b border-border">
              <th scope="col" className={HEAD}>
                {t('audit.diff.key')}
              </th>
              <th scope="col" className={HEAD}>
                {t('audit.diff.value')}
              </th>
            </tr>
          </thead>
          <tbody>
            {details.map(([key, value]) => (
              <tr key={key} className="border-b border-border">
                <th
                  scope="row"
                  className={`${CELL} text-left font-mono text-xs font-normal break-all`}
                >
                  {key}
                </th>
                <td className={CELL}>
                  <AuditValue value={value} accounts={event.accounts} />
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      ) : null}
      {changes.length === 0 && details.length === 0 ? (
        <p>{t('audit.diff.empty')}</p>
      ) : null}
      {event.request_id ? (
        <p className="break-all">
          <span className="font-medium">{t('audit.diff.requestId')}</span>{' '}
          <span className="font-mono text-xs">{event.request_id}</span>
        </p>
      ) : null}
    </div>
  );
}
