import { useState } from 'react';
import { useTranslation } from 'react-i18next';
import { ErrorAlert } from '@/components/feedback/ErrorAlert';
import { LoadingState } from '@/components/feedback/LoadingState';
import { Spinner } from '@/components/feedback/Spinner';
import { Button } from '@/components/ui/button';
import { ConfirmDialog } from '@/features/guardians/ConfirmDialog';
import { isExportDataset } from './access';
import { SaveSetupDialog, type SetupFields } from './SaveSetupDialog';
import type { ReportSetup } from './setups';

// "Configuraciones guardadas" (M8-08 D1, D2): the caller's setups and those
// shared with one of their roles, by name. Each row shows the dataset, the
// format, "Compartida con <rol>" and, for another user's, "de <nombre>".
// Ejecutar and Cargar en el formulario need `can_run`; otherwise the row
// says the data is no longer theirs. Editar (name and sharing) and Eliminar
// (confirmed) are for the caller's own setups. The page runs, loads and
// announces; this component only renders and asks.

export type SavedSetupsState =
  | { status: 'loading' }
  | { status: 'error'; error: unknown; onRetry: () => void }
  | { status: 'ready'; items: readonly ReportSetup[] };

interface SavedSetupsProps {
  state: SavedSetupsState;
  /** The setup being run (its button shows a spinner). */
  runningId: string | null;
  /** A message about the last action (the export limit, a stale list). */
  notice: string | null;
  /** A failed run the central error map explains. */
  failure: unknown;
  onRun: (setup: ReportSetup) => void;
  onLoad: (setup: ReportSetup) => void;
  onEdit: (setup: ReportSetup, fields: SetupFields) => Promise<void>;
  onDelete: (setup: ReportSetup) => Promise<void>;
  /** 409 version_conflict on an edit: reload the list. */
  onStale: () => void;
}

const ACTION = 'min-h-11 md:min-h-9';

export function SavedSetups({
  state,
  runningId,
  notice,
  failure,
  onRun,
  onLoad,
  onEdit,
  onDelete,
  onStale,
}: SavedSetupsProps) {
  const { t } = useTranslation();
  const [editing, setEditing] = useState<ReportSetup | null>(null);
  const [deleting, setDeleting] = useState<ReportSetup | null>(null);

  if (state.status === 'loading') {
    return <LoadingState rows={2} />;
  }
  if (state.status === 'error') {
    return <ErrorAlert error={state.error} onRetry={state.onRetry} />;
  }

  const datasetName = (code: string) =>
    isExportDataset(code) ? t(`exports.datasets.${code}`) : code;

  return (
    <div className="flex min-w-0 flex-col gap-4">
      {notice ? (
        <p role="alert" className="text-sm text-destructive">
          {notice}
        </p>
      ) : null}
      {failure ? <ErrorAlert error={failure} /> : null}
      {state.items.length === 0 ? (
        <p className="text-sm text-muted-foreground">
          {t('exports.setups.empty')}
        </p>
      ) : (
        <ul className="flex min-w-0 flex-col divide-y divide-border rounded-lg border border-border">
          {state.items.map((setup) => (
            <li
              key={setup.id}
              data-setup-id={setup.id}
              className="flex min-w-0 flex-col gap-3 p-4 md:flex-row md:items-center md:justify-between"
            >
              <div className="flex min-w-0 flex-col gap-1">
                <p className="font-medium break-words">{setup.name}</p>
                <p className="text-sm text-muted-foreground">
                  {t('exports.setups.summary', {
                    dataset: datasetName(setup.dataset),
                    format: t(`exports.formats.${setup.parameters.format}`),
                  })}
                </p>
                <div className="flex flex-wrap gap-2 text-sm">
                  {setup.shared_role ? (
                    <span className="rounded-full border border-border px-2 py-0.5">
                      {t('exports.setups.sharedWith', {
                        role: t(`roles.${setup.shared_role}`),
                      })}
                    </span>
                  ) : null}
                  {setup.mine ? null : (
                    <span className="text-muted-foreground">
                      {t('exports.setups.by', {
                        name: setup.created_by.display_name,
                      })}
                    </span>
                  )}
                </div>
              </div>
              <div className="flex flex-wrap gap-2">
                {setup.can_run ? (
                  <>
                    <Button
                      type="button"
                      className={ACTION}
                      // While it runs, the spinner's text is the name.
                      aria-label={
                        runningId === setup.id
                          ? undefined
                          : t('exports.setups.runFor', { name: setup.name })
                      }
                      disabled={runningId !== null}
                      onClick={() => onRun(setup)}
                    >
                      {runningId === setup.id ? (
                        <Spinner
                          label={t('exports.setups.running')}
                          showLabel
                        />
                      ) : (
                        t('exports.setups.run')
                      )}
                    </Button>
                    <Button
                      type="button"
                      variant="outline"
                      className={ACTION}
                      aria-label={t('exports.setups.loadFor', {
                        name: setup.name,
                      })}
                      onClick={() => onLoad(setup)}
                    >
                      {t('exports.setups.load')}
                    </Button>
                  </>
                ) : (
                  <p className="self-center text-sm text-muted-foreground">
                    {t('exports.setups.noAccess')}
                  </p>
                )}
                {setup.mine ? (
                  <>
                    {setup.can_run ? (
                      <Button
                        type="button"
                        variant="outline"
                        className={ACTION}
                        aria-label={t('exports.setups.editFor', {
                          name: setup.name,
                        })}
                        onClick={() => setEditing(setup)}
                      >
                        {t('exports.setups.edit')}
                      </Button>
                    ) : null}
                    <Button
                      type="button"
                      variant="outline"
                      className={ACTION}
                      aria-label={t('exports.setups.deleteFor', {
                        name: setup.name,
                      })}
                      onClick={() => setDeleting(setup)}
                    >
                      {t('exports.setups.delete')}
                    </Button>
                  </>
                ) : null}
              </div>
            </li>
          ))}
        </ul>
      )}
      <SaveSetupDialog
        open={editing !== null}
        title={t('exports.setups.editTitle')}
        description={t('exports.setups.editDescription')}
        initial={{
          name: editing?.name ?? '',
          sharedRole: editing?.shared_role ?? null,
        }}
        submitLabel={t('exports.setups.saveChanges')}
        onSubmit={(fields) => onEdit(editing!, fields)}
        onStale={onStale}
        onClose={() => setEditing(null)}
      />
      <ConfirmDialog
        open={deleting !== null}
        title={t('exports.setups.deleteTitle', { name: deleting?.name ?? '' })}
        body={t('exports.setups.deleteBody')}
        confirmLabel={t('exports.setups.delete')}
        destructive
        onConfirm={async () => {
          const target = deleting!;
          await onDelete(target);
          setDeleting(null);
        }}
        onCancel={() => setDeleting(null)}
      />
    </div>
  );
}
