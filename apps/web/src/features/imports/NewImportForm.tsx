import { useQueryClient } from '@tanstack/react-query';
import { useId, useRef, useState, type FormEvent } from 'react';
import { useTranslation } from 'react-i18next';
import { useNavigate } from 'react-router';
import { Spinner } from '@/components/feedback/Spinner';
import { Button } from '@/components/ui/button';
import { cn } from '@/lib/utils';
import {
  createImport,
  IMPORTS_LIST_KEY,
  importKey,
  MAX_FILE_BYTES,
} from './api';
import { createErrorMessage, fileTooLargeMessage } from './codes';
import {
  templateCsv,
  templateFileName,
  type ImportKindCode,
  type ImportKindDefinition,
} from './kinds';
import { SELECT_CLASS } from './styles';

// "New import" (M8-05b B1-B3, E2): the kind, its columns and template, and
// the CSV. The browser reads the file (`file.text()`) and posts its text in
// a JSON body; nothing is stored in the browser or on a server. A file over
// 2 MiB is refused before anything is sent. A refused file's message is
// linked to the file input (aria-describedby); a staged import opens its
// report.

/** The API's `file_name` limit. */
const FILE_NAME_MAX_LENGTH = 255;

interface NewImportFormProps {
  kinds: readonly ImportKindDefinition[];
  /** The kind to preselect (`?kind=`), when the user may import it. */
  initialKind: ImportKindCode | '';
}

/** Saves `text` as a file through a temporary object URL. */
function downloadText(text: string, fileName: string) {
  const url = URL.createObjectURL(
    new Blob([text], { type: 'text/csv;charset=utf-8' }),
  );
  const link = document.createElement('a');
  link.href = url;
  link.download = fileName;
  document.body.append(link);
  link.click();
  link.remove();
  URL.revokeObjectURL(url);
}

export function NewImportForm({ kinds, initialKind }: NewImportFormProps) {
  const { t } = useTranslation();
  const { t: tErrors } = useTranslation('errors');
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const id = useId();
  const ids = {
    kind: `${id}-kind`,
    kindError: `${id}-kind-error`,
    columns: `${id}-columns`,
    file: `${id}-file`,
    fileHelp: `${id}-file-help`,
    fileError: `${id}-file-error`,
  };
  const single = kinds.length === 1 ? kinds[0]!.code : '';
  const [kindCode, setKindCode] = useState<ImportKindCode | ''>(
    initialKind || single,
  );
  const [shownInitial, setShownInitial] = useState(initialKind);
  if (initialKind !== shownInitial) {
    // A link changed ?kind= while the page was open (adjusting state while
    // rendering, not in an effect).
    setShownInitial(initialKind);
    if (initialKind) {
      setKindCode(initialKind);
    }
  }
  const [kindError, setKindError] = useState<string | null>(null);
  const [fileError, setFileError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const fileInput = useRef<HTMLInputElement>(null);
  const kindSelect = useRef<HTMLSelectElement>(null);

  const kind = kinds.find((candidate) => candidate.code === kindCode);

  function chosenFile(): File | undefined {
    return fileInput.current?.files?.[0];
  }

  function onFileChange() {
    const file = chosenFile();
    setFileError(
      file && file.size > MAX_FILE_BYTES ? fileTooLargeMessage(t) : null,
    );
  }

  function refuseFile(message: string) {
    setFileError(message);
    fileInput.current?.focus();
  }

  async function onSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (busy) {
      return;
    }
    if (!kind) {
      setKindError(t('imports.form.kindRequired'));
      kindSelect.current?.focus();
      return;
    }
    setKindError(null);
    const file = chosenFile();
    if (!file) {
      refuseFile(t('imports.file.required'));
      return;
    }
    if (file.size > MAX_FILE_BYTES) {
      refuseFile(fileTooLargeMessage(t));
      return;
    }
    setFileError(null);
    setBusy(true);
    try {
      const csv = await file.text();
      const fileName = file.name.trim().slice(0, FILE_NAME_MAX_LENGTH);
      const job = await createImport({
        kind: kind.code,
        file_name: fileName || null,
        csv,
      });
      queryClient.setQueryData(importKey(job.id), job);
      void queryClient.invalidateQueries({ queryKey: IMPORTS_LIST_KEY });
      void navigate(`/imports/${job.id}`);
    } catch (error) {
      setBusy(false);
      refuseFile(createErrorMessage(error, t, tErrors));
    }
  }

  const required = kind?.columns.filter((column) => column.required) ?? [];
  const optional = kind?.columns.filter((column) => !column.required) ?? [];

  return (
    <form
      noValidate
      className="flex max-w-2xl flex-col gap-6"
      onSubmit={(event) => void onSubmit(event)}
    >
      <div className="flex flex-col gap-2">
        <label htmlFor={ids.kind} className="text-sm font-medium">
          {t('imports.form.kind')}
        </label>
        <select
          id={ids.kind}
          ref={kindSelect}
          className={cn(SELECT_CLASS, 'min-h-11 md:min-h-9')}
          value={kindCode}
          aria-invalid={kindError ? true : undefined}
          aria-describedby={kindError ? ids.kindError : undefined}
          onChange={(event) => {
            setKindCode(event.target.value as ImportKindCode | '');
            setKindError(null);
          }}
        >
          {kinds.length > 1 ? (
            <option value="">{t('imports.form.kindPlaceholder')}</option>
          ) : null}
          {kinds.map((candidate) => (
            <option key={candidate.code} value={candidate.code}>
              {t(`imports.kinds.${candidate.code}`)}
            </option>
          ))}
        </select>
        {kindError ? (
          <p id={ids.kindError} className="text-sm text-destructive">
            {kindError}
          </p>
        ) : null}
      </div>

      {kind ? (
        <section
          aria-labelledby={ids.columns}
          className="flex flex-col gap-3 rounded-md border border-border p-4"
        >
          <h3 id={ids.columns} className="font-medium">
            {t('imports.form.columnsTitle')}
          </h3>
          <dl className="grid gap-2 text-sm">
            <dt className="font-medium">{t('imports.form.requiredColumns')}</dt>
            <dd className="flex flex-wrap gap-x-2 gap-y-1">
              {required.map((column) => (
                <code key={column.name} className="break-all">
                  {column.name}
                </code>
              ))}
            </dd>
            {optional.length > 0 ? (
              <>
                <dt className="font-medium">
                  {t('imports.form.optionalColumns')}
                </dt>
                <dd className="flex flex-wrap gap-x-2 gap-y-1">
                  {optional.map((column) => (
                    <code key={column.name} className="break-all">
                      {column.name}
                    </code>
                  ))}
                </dd>
              </>
            ) : null}
          </dl>
          {kind.code === 'tutors' ? (
            <p className="text-sm">{t('imports.form.tutorsHelp')}</p>
          ) : null}
          <div>
            <Button
              type="button"
              variant="outline"
              className="min-h-11 md:min-h-9"
              onClick={() =>
                downloadText(templateCsv(kind), templateFileName(kind))
              }
            >
              {t('imports.form.downloadTemplate')}
            </Button>
          </div>
        </section>
      ) : null}

      <div className="flex flex-col gap-2">
        <label htmlFor={ids.file} className="text-sm font-medium">
          {t('imports.form.file')}
        </label>
        <input
          id={ids.file}
          ref={fileInput}
          type="file"
          accept=".csv,text/csv"
          className="min-h-11 text-sm file:mr-3 file:min-h-11 file:rounded-md file:border file:border-border file:bg-background file:px-3 file:text-sm file:font-medium focus-visible:ring-3 focus-visible:ring-ring focus-visible:outline-none md:min-h-9 md:file:min-h-9"
          aria-invalid={fileError ? true : undefined}
          aria-describedby={
            fileError ? `${ids.fileError} ${ids.fileHelp}` : ids.fileHelp
          }
          onChange={onFileChange}
        />
        <p id={ids.fileHelp} className="text-sm text-muted-foreground">
          {t('imports.form.csvHelp')}{' '}
          <strong>{t('imports.form.csvFormat')}</strong>.
        </p>
        {fileError ? (
          <p
            id={ids.fileError}
            role="alert"
            className="text-sm text-destructive"
          >
            {fileError}
          </p>
        ) : null}
      </div>

      <div>
        <Button type="submit" className="min-h-11 md:min-h-9" disabled={busy}>
          {busy ? (
            <Spinner label={t('imports.form.submitting')} showLabel />
          ) : (
            t('imports.form.submit')
          )}
        </Button>
      </div>
    </form>
  );
}
