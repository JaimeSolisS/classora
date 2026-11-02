import type { Meta, StoryObj } from '@storybook/react-vite';
import type { ReactNode } from 'react';
import { expect, fn, within } from 'storybook/test';
import { ApiError } from '@/app/api';
import { i18n } from '@/i18n';
import type { LocalDate } from '@/lib/time';
import { StoryAuth } from '@/test/StoryAuth';
import { StoryRouter } from '@/test/StoryRouter';
import {
  ITEM_LONG,
  ITEM_PAID,
  ITEM_RECURRING,
  ITEM_WAITING,
  PAY_ITEM_TYPES,
  PAY_ITEMS,
  TODAY,
  TYPE_BONUS,
  TYPE_RETIRED,
} from './fixtures';
import { PayItemDialog } from './PayItemDialog';
import { PayItemEndDialog } from './PayItemEndDialog';
import { PayItemsView, type PayItemActions } from './PayItemsView';
import { PayItemTypeDialog } from './PayItemTypeDialog';
import { PayItemTypesView } from './PayItemTypesView';
import { FINANCE_SESSION } from './storySession';

// The tutor's bonuses and deductions, and every dialog (#454 F2): the section's
// states, read-only, 320 px, a long description, each dialog's refusals, and the
// types page.
// No request. axe runs on each story in both
// themes.

const narrowViewport = {
  viewport: {
    options: {
      narrow320: {
        name: 'Narrow (320 px)',
        styles: { width: '320px', height: '640px' },
        type: 'mobile',
      },
    },
  },
};
const narrow = {
  parameters: narrowViewport,
  globals: { viewport: { value: 'narrow320' } },
};

const ready = (items = PAY_ITEMS, hasMore = false) =>
  ({
    status: 'ready',
    items,
    hasMore,
    loadingMore: false,
    moreError: null,
    onMore: fn(),
    refreshError: null,
    onRefresh: fn(),
  }) as const;

const actions: PayItemActions = {
  onAdd: fn(),
  onEnd: fn(),
  onDelete: fn(() => Promise.resolve()),
};

const refused = (code: string, status = 409) => new ApiError({ status, code });

const today = TODAY as LocalDate;

function Shell({ children }: { children: ReactNode }) {
  return (
    <StoryAuth session={FINANCE_SESSION}>
      <StoryRouter
        path="/payroll/tutors/1"
        routes={[{ path: '*', element: <main>{children}</main> }]}
      />
    </StoryAuth>
  );
}

const meta = {
  title: 'Payroll/PayItems',
  component: PayItemsView,
  args: { state: ready(), actions },
  render: (args) => (
    <Shell>
      <PayItemsView {...args} />
    </Shell>
  ),
} satisfies Meta<typeof PayItemsView>;

export default meta;
type Story = StoryObj<typeof meta>;

/** Every state of B2, with the controls. */
export const Section: Story = {
  args: { state: ready(PAY_ITEMS, true) },
  play: async ({ canvas }) => {
    // Table and cards both hold each row; `md` shows the table.
    const table = within(canvas.getByRole('table'));
    await expect(
      table.getByText(i18n.t('payroll.payItems.lands.waiting')),
    ).toBeVisible();
    await expect(table.getByText('-$1,250.50')).toBeVisible();
    await expect(
      canvas.getByText(i18n.t('payroll.payItems.note')),
    ).toBeVisible();
  },
};

/** The refresh failed: the rows may be old, and the retry is offered. */
export const RefreshFailed: Story = {
  args: {
    state: {
      ...ready([ITEM_WAITING]),
      refreshError: refused('internal_error', 502),
    },
  },
  play: async ({ canvas }) => {
    await expect(canvas.getByRole('alert')).toHaveTextContent(
      i18n.t('payroll.payItems.stale'),
    );
    await expect(
      canvas.getByRole('button', {
        name: i18n.t('payroll.payItems.reloadList'),
      }),
    ).toBeVisible();
  },
};

export const ReadOnly: Story = { args: { actions: undefined } };

export const Empty: Story = { args: { state: ready([]) } };

export const Loading: Story = { args: { state: { status: 'loading' } } };

export const Failed: Story = {
  args: {
    state: {
      status: 'error',
      error: refused('permission_denied', 403),
      onRetry: fn(),
    },
  },
};

/** D3: phone width, cards, 44 px controls and no sideways scroll. */
export const Narrow: Story = {
  ...narrow,
  args: { state: ready([...PAY_ITEMS, ITEM_LONG]) },
  play: async ({ canvas, canvasElement }) => {
    await expect(canvasElement.scrollWidth).toBeLessThanOrEqual(
      canvasElement.clientWidth,
    );
    const add = canvas.getByRole('button', {
      name: i18n.t('payroll.payItems.add'),
    });
    await expect(add.getBoundingClientRect().height).toBeGreaterThanOrEqual(44);
  },
};

/** A long description wraps; the table keeps its width (edge case). */
export const LongDescription: Story = {
  args: { state: ready([ITEM_LONG, ITEM_RECURRING]) },
  play: async ({ canvasElement }) => {
    await expect(canvasElement.scrollWidth).toBeLessThanOrEqual(
      canvasElement.clientWidth,
    );
  },
};

export const DeleteRefused: Story = {
  args: {
    initialDelete: ITEM_WAITING,
    initialDeleteError: refused('pay_item_in_use'),
  },
};

const typesReady = { status: 'ready', types: PAY_ITEM_TYPES } as const;
const dialogArgs = {
  open: true,
  today,
  onSubmit: fn(() => Promise.resolve()),
  onReloadTypes: fn(() => Promise.resolve()),
  onClose: fn(),
};

export const AddDialog: Story = {
  render: () => (
    <Shell>
      <PayItemDialog {...dialogArgs} types={typesReady} />
    </Shell>
  ),
};

export const AddRecurring: Story = {
  render: () => (
    <Shell>
      <PayItemDialog
        {...dialogArgs}
        types={typesReady}
        initialValues={{ recurrence: 'recurring' }}
      />
    </Shell>
  ),
};

export const AddRefused: Story = {
  render: () => (
    <Shell>
      <PayItemDialog
        {...dialogArgs}
        types={typesReady}
        initialValues={{ typeId: TYPE_BONUS.id, amount: '0' }}
        initialErrors={{
          typeId: 'errors:pay_item_type_inactive',
          description: 'payroll.validation.descriptionRequired',
          amount: 'payroll.validation.amountPositive',
        }}
      />
    </Shell>
  ),
};

/** With no active type the dialog links to the types page (B3). */
export const AddNoTypes: Story = {
  render: () => (
    <Shell>
      <PayItemDialog
        {...dialogArgs}
        types={{ status: 'ready', types: [TYPE_RETIRED] }}
      />
    </Shell>
  ),
};

export const AddTypesLoading: Story = {
  render: () => (
    <Shell>
      <PayItemDialog {...dialogArgs} types={{ status: 'loading' }} />
    </Shell>
  ),
};

export const AddDialogNarrow: Story = {
  ...narrow,
  render: () => (
    <Shell>
      <PayItemDialog
        {...dialogArgs}
        types={typesReady}
        initialValues={{ recurrence: 'recurring' }}
      />
    </Shell>
  ),
};

const endArgs = {
  open: true,
  item: ITEM_RECURRING,
  today,
  onSubmit: fn(() => Promise.resolve()),
  onReload: fn(() => Promise.resolve()),
  onClose: fn(),
};

export const EndDialog: Story = {
  render: () => (
    <Shell>
      <PayItemEndDialog {...endArgs} />
    </Shell>
  ),
};

export const EndRefused: Story = {
  render: () => (
    <Shell>
      <PayItemEndDialog
        {...endArgs}
        initialError="errors:pay_item_end_before_paid_period"
      />
    </Shell>
  ),
};

/** The paid deduction: no delete, and End only when recurring. */
export const AppliedRow: Story = { args: { state: ready([ITEM_PAID]) } };

const typeArgs = {
  open: true,
  onSubmit: fn(() => Promise.resolve()),
  onReload: fn(() => Promise.resolve()),
  onClose: fn(),
};

export const TypeAddDialog: Story = {
  render: () => (
    <Shell>
      <PayItemTypeDialog {...typeArgs} type={null} />
    </Shell>
  ),
};

export const TypeEditDialog: Story = {
  render: () => (
    <Shell>
      <PayItemTypeDialog {...typeArgs} type={TYPE_BONUS} />
    </Shell>
  ),
};

export const TypeRefused: Story = {
  render: () => (
    <Shell>
      <PayItemTypeDialog
        {...typeArgs}
        type={null}
        initialValues={{ name: 'Bono', direction: 'bonus' }}
        initialErrors={{ name: 'errors:pay_item_type_exists' }}
      />
    </Shell>
  ),
};

const typesViewArgs = {
  canManage: true,
  onAdd: fn(),
  onEdit: fn(),
  onSetActive: fn(() => Promise.resolve()),
};

/** The types page. */
export const Types: Story = {
  render: () => (
    <Shell>
      <PayItemTypesView {...typesViewArgs} state={typesReady} />
    </Shell>
  ),
};

export const TypesReadOnly: Story = {
  render: () => (
    <Shell>
      <PayItemTypesView
        {...typesViewArgs}
        canManage={false}
        state={typesReady}
      />
    </Shell>
  ),
};

export const TypesEmpty: Story = {
  render: () => (
    <Shell>
      <PayItemTypesView
        {...typesViewArgs}
        state={{ status: 'ready', types: [] }}
      />
    </Shell>
  ),
};

export const TypesNarrow: Story = {
  ...narrow,
  render: () => (
    <Shell>
      <PayItemTypesView {...typesViewArgs} state={typesReady} />
    </Shell>
  ),
};

export const TypeDeactivateRefused: Story = {
  render: () => (
    <Shell>
      <PayItemTypesView
        {...typesViewArgs}
        state={typesReady}
        initialToggle={TYPE_BONUS}
        initialToggleError={refused('pay_item_type_in_use')}
      />
    </Shell>
  ),
};
