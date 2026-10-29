import { useId } from 'react';
import { useTranslation } from 'react-i18next';
import { Link } from 'react-router';
import { ErrorAlert } from '@/components/feedback/ErrorAlert';
import { LoadingState } from '@/components/feedback/LoadingState';
import { Button } from '@/components/ui/button';
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from '@/components/ui/table';
import { Pagination } from '@/features/catalog/Pagination';
import { SearchPicker } from '@/features/guardians/SearchPicker';
import { LINK_BUTTON_CLASS } from '@/features/students/styles';
import { BillingTabs } from '../BillingTabs';
import {
  PLAN_STATUSES,
  searchStudents,
  TUITION_OPTIONS_KEY,
  type PlanListFilters,
  type StudentOption,
  type TuitionPlanPage,
} from './api';
import { useTuitionLabels } from './labels';
import { PlanStatusBadge } from './TuitionBadges';

// The tuition plan list (#374 C), without any query (stories render each
// state): the Billing tabs, the title with "New plan" (billing.manage
// only), the student filter (the #34 search picker, or the chosen student
// with "Show all students") and the status checkboxes (none = every
// status; the page keeps both in the URL), the table and the pagination.
// Below `md` the table scrolls inside its own container (SC 1.4.10
// data-table exception).

export type TuitionPlansState =
  | { status: 'loading' }
  | { status: 'error'; error: unknown; onRetry: () => void }
  | { status: 'ready'; page: TuitionPlanPage };

export interface TuitionPlansViewProps {
  state: TuitionPlansState;
  filters: PlanListFilters;
  /** The filtered student's name (null when unknown). */
  studentName: string | null;
  onStudent: (student: StudentOption | null) => void;
  onFilters: (filters: PlanListFilters) => void;
  onPage: (page: number) => void;
  /** billing.manage: "New plan". */
  canManage: boolean;
  onAdd: () => void;
  /** The list's search string, carried to the plan page's back link. */
  listSearch: string;
  /** The last change, announced politely. */
  announcement?: string;
  /** Stories: the picker's search instead of the API. */
  search?: (q: string, signal: AbortSignal) => Promise<StudentOption[]>;
}

export function TuitionPlansView({
  state,
  filters,
  studentName,
  onStudent,
  onFilters,
  onPage,
  canManage,
  onAdd,
  listSearch,
  announcement = '',
  search = searchStudents,
}: TuitionPlansViewProps) {
  const { t } = useTranslation();
  const labels = useTuitionLabels();
  const id = useId();
  const ids = { title: `${id}-title`, status: `${id}-status` };

  const filtered = filters.statuses.length > 0 || filters.studentId !== null;

  function toggleStatus(status: (typeof PLAN_STATUSES)[number]) {
    const has = filters.statuses.includes(status);
    onFilters({
      ...filters,
      statuses: PLAN_STATUSES.filter((item) =>
        item === status ? !has : filters.statuses.includes(item),
      ),
      page: 1,
    });
  }

  let body;
  if (state.status === 'loading') {
    body = <LoadingState layout="table" rows={5} />;
  } else if (state.status === 'error') {
    body = <ErrorAlert error={state.error} onRetry={state.onRetry} />;
  } else if (state.page.items.length === 0 && filtered) {
    body = <p>{t('billing.tuition.emptyFiltered')}</p>;
  } else if (state.page.items.length === 0) {
    body = (
      <div className="flex flex-col items-start gap-3">
        <p>{t('billing.tuition.empty')}</p>
        {canManage ? (
          <Button type="button" variant="outline" onClick={onAdd}>
            {t('billing.tuition.add')}
          </Button>
        ) : null}
      </div>
    );
  } else {
    const { items, total, limit, offset } = state.page;
    body = (
      <div className="flex flex-col gap-4">
        <Table aria-labelledby={ids.title}>
          <TableHeader>
            <TableRow>
              <TableHead scope="col">
                {t('billing.tuition.columns.student')}
              </TableHead>
              <TableHead scope="col">
                {t('billing.tuition.columns.family')}
              </TableHead>
              <TableHead scope="col">
                {t('billing.tuition.columns.plan')}
              </TableHead>
              <TableHead scope="col">
                {t('billing.tuition.columns.amount')}
              </TableHead>
              <TableHead scope="col">
                {t('billing.tuition.columns.startsOn')}
              </TableHead>
              <TableHead scope="col">
                {t('billing.tuition.columns.endsOn')}
              </TableHead>
              <TableHead scope="col">
                {t('billing.tuition.columns.status')}
              </TableHead>
              <TableHead scope="col">
                <span className="sr-only">
                  {t('billing.tuition.columns.actions')}
                </span>
              </TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {items.map((plan) => (
              <TableRow key={plan.id} data-tuition-plan-id={plan.id}>
                <TableCell className="max-w-xs whitespace-normal break-words">
                  {plan.student.name}
                </TableCell>
                <TableCell className="max-w-xs whitespace-normal break-words">
                  {plan.family.name}
                </TableCell>
                <TableCell className="max-w-xs whitespace-normal break-words">
                  <span className="block">{labels.subject(plan)}</span>
                  <span className="block text-sm text-muted-foreground">
                    {t('billing.tuition.scopeLine', {
                      scope: labels.scope(plan),
                      frequency: labels.frequency(plan.frequency),
                    })}
                  </span>
                </TableCell>
                <TableCell>{labels.amount(plan)}</TableCell>
                <TableCell>{labels.startsOn(plan)}</TableCell>
                <TableCell>{labels.endsOn(plan)}</TableCell>
                <TableCell>
                  <PlanStatusBadge status={plan.status} />
                </TableCell>
                <TableCell className="text-right">
                  <Link
                    to={`/billing/tuition-plans/${plan.id}`}
                    state={{ listSearch }}
                    className={LINK_BUTTON_CLASS}
                    aria-label={t('billing.tuition.viewFor', {
                      student: plan.student.name,
                      plan: labels.plan(plan),
                    })}
                  >
                    {t('billing.tuition.view')}
                  </Link>
                </TableCell>
              </TableRow>
            ))}
          </TableBody>
        </Table>
        <Pagination
          offset={offset}
          limit={limit}
          shown={items.length}
          total={total}
          onPage={onPage}
        />
      </div>
    );
  }

  return (
    <div className="flex flex-col gap-8">
      <BillingTabs />
      <div className="flex flex-col gap-2">
        <div className="flex flex-wrap items-center justify-between gap-4">
          <h1 id={ids.title} className="text-2xl font-semibold">
            {t('billing.tuition.title')}
          </h1>
          {canManage ? (
            <Button type="button" onClick={onAdd}>
              {t('billing.tuition.add')}
            </Button>
          ) : null}
        </div>
        <p className="text-sm text-muted-foreground">
          {t('billing.tuition.intro')}
        </p>
      </div>
      <div
        role="search"
        aria-label={t('billing.tuition.filters.label')}
        className="grid grid-cols-1 gap-4 md:grid-cols-[minmax(0,2fr)_minmax(0,1fr)] lg:max-w-3xl"
      >
        {filters.studentId ? (
          <div className="flex min-w-0 flex-col items-start gap-2">
            <p className="text-sm font-medium">
              {t('billing.tuition.filters.student')}
            </p>
            <p className="break-words">
              {studentName ?? t('billing.tuition.filters.unknownStudent')}
            </p>
            <Button
              type="button"
              variant="outline"
              size="sm"
              onClick={() => onStudent(null)}
            >
              {t('billing.tuition.filters.clearStudent')}
            </Button>
          </div>
        ) : (
          <SearchPicker<StudentOption>
            label={t('billing.tuition.filters.searchStudent')}
            help={t('billing.tuition.filters.searchHelp')}
            queryKey={[...TUITION_OPTIONS_KEY, 'filter-students']}
            search={search}
            optionId={(option) => option.id}
            optionLabel={(option) => option.name}
            selectedId={null}
            onSelect={(student) => onStudent(student)}
          />
        )}
        <fieldset className="flex min-w-0 flex-col gap-2">
          <legend className="mb-2 text-sm font-medium">
            {t('billing.tuition.filters.status')}
          </legend>
          {PLAN_STATUSES.map((status) => (
            <label
              key={status}
              className="flex min-h-6 items-center gap-2 text-sm"
            >
              <input
                type="checkbox"
                className="size-4 accent-primary"
                checked={filters.statuses.includes(status)}
                onChange={() => toggleStatus(status)}
              />
              {t(`billing.tuition.status.${status}`)}
            </label>
          ))}
          <p className="text-sm text-muted-foreground">
            {t('billing.tuition.filters.statusHelp')}
          </p>
        </fieldset>
      </div>
      <p role="status" className="text-sm">
        {announcement}
      </p>
      {body}
    </div>
  );
}
