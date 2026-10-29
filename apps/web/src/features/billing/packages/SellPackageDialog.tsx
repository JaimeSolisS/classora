import { useId, useRef, useState, type FormEvent } from 'react';
import { useTranslation } from 'react-i18next';
import { Link } from 'react-router';
import { ApiError } from '@/app/api';
import { ErrorAlert } from '@/components/feedback/ErrorAlert';
import { LoadingState } from '@/components/feedback/LoadingState';
import { Spinner } from '@/components/feedback/Spinner';
import { Button } from '@/components/ui/button';
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog';
import { FieldError } from '@/features/auth/FieldError';
import { SearchPicker } from '@/features/guardians/SearchPicker';
import {
  useFieldMessage,
  useReturnFocus,
} from '@/features/scheduling/series/hooks';
import { SELECT_CLASS, TEXT_LINK_CLASS } from '@/features/students/styles';
import { useIdempotentSubmission } from '@/lib/idempotency';
import type { LocalDate } from '@/lib/time';
import { searchStudents, type StudentOption } from '../tuition/api';
import {
  PACKAGE_OPTIONS_KEY,
  type PackageProduct,
  type PackagePurchase,
} from './api';
import { usePackageLabels } from './labels';
import {
  expiresOn,
  isOffered,
  sellServerErrors,
  type SellErrors,
  type SellField,
} from './rules';

// "Sell package" (#377 D), without any package call: the student (the #34
// search picker over active students) and an active product (one with no
// price in effect today is listed disabled). A summary reads what the
// sale charges today, before discounts, and when the package expires.
//
// A sale posts a ledger row, so it is an idempotent submission (#88): the
// key is computed from the body with `keyFor` BEFORE `onSubmit` runs the
// call, so a second press after a network error or a 5xx resends the same
// key (the server replays the first sale instead of selling twice), and
// `succeeded()` gives the next sale a new key. 409 student_has_no_family is
// an alert with a link to the student for student.view holders; 409
// package_product_inactive / package_not_offered are alerts (and reload
// the products); a 422 goes on its field. Nothing chosen is lost.

export interface SellPackageDialogProps {
  open: boolean;
  /** Today's date in the center's zone (the expiry summary). */
  today: LocalDate;
  /** The active products; null while they load. */
  products: readonly PackageProduct[] | null;
  productsError?: unknown;
  onRetryProducts?: () => void;
  /** student.view: the no-family alert links to the student's page. */
  canOpenStudents: boolean;
  /** Sells the package with the key; rejects with the error to show. */
  onSubmit: (body: PackagePurchase, idempotencyKey: string) => Promise<void>;
  onClose: () => void;
  /** Stories: the student search instead of the API. */
  search?: (q: string, signal: AbortSignal) => Promise<StudentOption[]>;
  /** Stories: the values to start from. */
  initialStudent?: StudentOption | null;
  initialProductId?: string;
}

export function SellPackageDialog(props: SellPackageDialogProps) {
  const busy = useRef(false);
  return (
    <Dialog
      open={props.open}
      onOpenChange={(next) => {
        if (!next && !busy.current) {
          props.onClose();
        }
      }}
    >
      {props.open ? <DialogBody {...props} busyRef={busy} /> : null}
    </Dialog>
  );
}

const PRODUCT_CODES = new Set([
  'package_product_inactive',
  'package_not_offered',
]);

function DialogBody({
  today,
  products,
  productsError,
  onRetryProducts,
  canOpenStudents,
  onSubmit,
  onClose,
  search = searchStudents,
  initialStudent = null,
  initialProductId = '',
  busyRef,
}: SellPackageDialogProps & { busyRef: { current: boolean } }) {
  const { t } = useTranslation();
  const { t: tErrors } = useTranslation('errors');
  const message = useFieldMessage();
  const labels = usePackageLabels();
  const returnFocus = useReturnFocus();
  const submission = useIdempotentSubmission();
  const id = useId();
  const [student, setStudent] = useState<StudentOption | null>(initialStudent);
  const [productId, setProductId] = useState(initialProductId);
  const [errors, setErrors] = useState<SellErrors>({});
  const [failure, setFailure] = useState<unknown>(null);
  const [submitting, setSubmitting] = useState(false);

  const fieldId = (field: SellField) => `${id}-${field}`;
  const errorId = (field: SellField) => `${id}-${field}-error`;
  const ids = {
    productHelp: `${id}-product-help`,
    summary: `${id}-summary`,
  };

  const product =
    products?.find((item) => item.id === productId && isOffered(item)) ?? null;

  function focusField(field: SellField) {
    const target = document.getElementById(fieldId(field));
    const focusable =
      target instanceof HTMLSelectElement
        ? target
        : target?.querySelector<HTMLElement>('input, select');
    focusable?.focus();
  }

  async function submit() {
    setFailure(null);
    const found: SellErrors = {};
    if (!student) found.student = 'billing.packages.validation.studentRequired';
    if (!product)
      found.productId = 'billing.packages.validation.productRequired';
    setErrors(found);
    if (!student || !product) {
      focusField(found.student ? 'student' : 'productId');
      return;
    }
    const body: PackagePurchase = {
      student_id: student.id,
      package_product_id: product.id,
    };
    // Outside the call (#88): a retry of the same body resends this key.
    const key = submission.keyFor(body);
    busyRef.current = true;
    setSubmitting(true);
    try {
      await onSubmit(body, key);
      submission.succeeded();
    } catch (caught) {
      const fromServer = sellServerErrors(caught);
      if (fromServer) {
        setErrors(fromServer);
        focusField(fromServer.student ? 'student' : 'productId');
      } else {
        setFailure(caught);
        if (caught instanceof ApiError && PRODUCT_CODES.has(caught.code)) {
          onRetryProducts?.();
        }
      }
    } finally {
      busyRef.current = false;
      setSubmitting(false);
    }
  }

  function onFormSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!busyRef.current) {
      void submit();
    }
  }

  const noFamily =
    failure instanceof ApiError && failure.code === 'student_has_no_family';
  const anyOffered = (products ?? []).some(isOffered);

  let content;
  if (productsError) {
    content = <ErrorAlert error={productsError} onRetry={onRetryProducts} />;
  } else if (!products) {
    content = <LoadingState rows={4} />;
  } else {
    content = (
      <form
        method="post"
        noValidate
        onSubmit={onFormSubmit}
        className="flex flex-col gap-4"
      >
        <div id={fieldId('student')}>
          <SearchPicker<StudentOption>
            label={t('billing.packages.fields.student')}
            help={
              student
                ? t('billing.packages.sell.chosenStudent', {
                    name: student.name,
                  })
                : t('billing.packages.hints.student')
            }
            queryKey={[...PACKAGE_OPTIONS_KEY, 'sell-students']}
            search={search}
            optionId={(option) => option.id}
            optionLabel={(option) => option.name}
            selectedId={student?.id ?? null}
            onSelect={(next) => {
              setStudent(next);
              setErrors((current) => ({ ...current, student: undefined }));
            }}
            errorMessage={message(errors.student)}
          />
        </div>
        <div className="flex min-w-0 flex-col gap-2">
          <label htmlFor={fieldId('productId')} className="text-sm font-medium">
            {t('billing.packages.fields.product')}
          </label>
          <select
            id={fieldId('productId')}
            className={SELECT_CLASS}
            value={productId}
            disabled={products.length === 0}
            aria-invalid={errors.productId ? true : undefined}
            aria-describedby={[
              ids.productHelp,
              errors.productId ? errorId('productId') : undefined,
            ]
              .filter(Boolean)
              .join(' ')}
            onChange={(event) => {
              setProductId(event.target.value);
              setErrors((current) => ({ ...current, productId: undefined }));
            }}
          >
            <option value="">{t('billing.prices.choose')}</option>
            {products.map((item) => (
              <option key={item.id} value={item.id} disabled={!isOffered(item)}>
                {labels.productOption(item)}
              </option>
            ))}
          </select>
          <p id={ids.productHelp} className="text-sm text-muted-foreground">
            {products.length === 0
              ? t('billing.packages.sell.noProducts')
              : !anyOffered
                ? t('billing.packages.sell.noneOffered')
                : t('billing.packages.sell.productHelp')}
          </p>
          <FieldError
            id={errorId('productId')}
            message={message(errors.productId)}
          />
        </div>
        <p id={ids.summary} className="text-sm font-medium" aria-live="polite">
          {product && product.current_amount !== null
            ? t('billing.packages.sell.summary', {
                amount: labels.money(product.current_amount),
                date: labels.date(expiresOn(product, today)),
              })
            : ''}
        </p>
        <p className="text-sm text-muted-foreground">
          {t('billing.packages.sell.notConverted')}
        </p>
        <DialogFooter>
          <Button
            type="button"
            variant="outline"
            disabled={submitting}
            onClick={onClose}
          >
            {t('billing.prices.cancel')}
          </Button>
          <Button type="submit" disabled={submitting}>
            {submitting ? (
              <Spinner label={t('billing.packages.sell.selling')} showLabel />
            ) : (
              t('billing.packages.sell.submit')
            )}
          </Button>
        </DialogFooter>
      </form>
    );
  }

  return (
    <DialogContent
      className="max-h-[90vh] overflow-y-auto sm:max-w-2xl"
      onOpenAutoFocus={returnFocus.onOpenAutoFocus}
      onCloseAutoFocus={returnFocus.onCloseAutoFocus}
      onInteractOutside={(event) => event.preventDefault()}
    >
      <DialogHeader>
        <DialogTitle>{t('billing.packages.sell.title')}</DialogTitle>
        <DialogDescription>{t('billing.packages.sell.help')}</DialogDescription>
      </DialogHeader>
      {noFamily ? (
        <div
          role="alert"
          className="flex flex-col items-start gap-2 rounded-md border border-destructive bg-background p-4"
        >
          <p className="font-medium text-destructive">
            {tErrors('student_has_no_family')}
          </p>
          {canOpenStudents && student ? (
            <Link to={`/students/${student.id}`} className={TEXT_LINK_CLASS}>
              {t('billing.packages.sell.openStudent', { name: student.name })}
            </Link>
          ) : null}
        </div>
      ) : failure ? (
        <ErrorAlert error={failure} />
      ) : null}
      {content}
    </DialogContent>
  );
}
