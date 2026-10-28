import { useId } from 'react';
import { useTranslation } from 'react-i18next';
import { SearchPicker } from '@/features/guardians/SearchPicker';
import { searchTargets, TARGET_KINDS, TARGETS_QUERY_KEY } from './targets';
import type { TargetKind, TargetOption } from './targets';

// A student or a family (#367 D2, E3, E4): a "Student / Family" radio and
// the #34 search picker over the chosen kind (the AddMemberDialog pattern).
// Changing the kind clears the choice. Options can be listed disabled with
// their own label (already assigned, the EnrollDialog pattern).

export interface TargetPickerProps {
  /** The radio group's legend. */
  legend: string;
  kind: TargetKind;
  onKind: (kind: TargetKind) => void;
  selected: TargetOption | null;
  onSelect: (target: TargetOption) => void;
  /** Options shown disabled (with `disabledLabel`). */
  disabledIds?: ReadonlySet<string>;
  disabledLabel?: (name: string) => string;
  errorMessage?: string;
  /** The search, injectable for stories. */
  search?: (
    kind: TargetKind,
    q: string,
    signal: AbortSignal,
  ) => Promise<TargetOption[]>;
}

export function TargetPicker({
  legend,
  kind,
  onKind,
  selected,
  onSelect,
  disabledIds,
  disabledLabel,
  errorMessage,
  search = searchTargets,
}: TargetPickerProps) {
  const { t } = useTranslation();
  const id = useId();
  const chosen = selected && selected.kind === kind ? selected : null;
  return (
    <div className="flex min-w-0 flex-col gap-4">
      <fieldset className="flex flex-col gap-2">
        <legend className="mb-2 text-sm font-medium">{legend}</legend>
        <div className="flex flex-wrap gap-x-6 gap-y-2">
          {TARGET_KINDS.map((value) => (
            <label
              key={value}
              className="flex min-h-6 items-center gap-2 text-sm"
            >
              <input
                type="radio"
                name={`${id}-kind`}
                value={value}
                checked={kind === value}
                className="size-4 accent-primary"
                onChange={() => onKind(value)}
              />
              {t(`billing.targets.kinds.${value}`)}
            </label>
          ))}
        </div>
      </fieldset>
      <SearchPicker<TargetOption>
        key={kind}
        label={t(`billing.targets.search.${kind}`)}
        help={
          chosen
            ? t('billing.targets.chosen', { name: chosen.name })
            : t(`billing.targets.searchHelp.${kind}`)
        }
        queryKey={[...TARGETS_QUERY_KEY, 'search', kind]}
        search={(q, signal) => search(kind, q, signal)}
        optionId={(option) => option.id}
        optionLabel={(option) =>
          disabledIds?.has(option.id) && disabledLabel
            ? disabledLabel(option.name)
            : option.name
        }
        optionDisabled={(option) => disabledIds?.has(option.id) ?? false}
        selectedId={chosen?.id ?? null}
        onSelect={onSelect}
        errorMessage={errorMessage}
      />
    </div>
  );
}
