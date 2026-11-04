import type { Meta, StoryObj } from '@storybook/react-vite';
import type { ReactNode } from 'react';
import { expect, fn, within } from 'storybook/test';
import { ApiError } from '@/app/api';
import { i18n } from '@/i18n';
import { StoryAuth } from '@/test/StoryAuth';
import { StoryRouter } from '@/test/StoryRouter';
import {
  CAT_CUSTOM,
  CAT_RENT,
  EXPENSES_SESSION,
  MIXED_CATEGORIES,
  SEEDED_CATEGORIES,
} from '../fixtures';
import { CategoriesView, type CategoriesState } from './CategoriesView';
import { CategoryDialog } from './CategoryDialog';

// The expense categories tab and its dialogs (M7-11b F2): seeded rows only,
// mixed with renamed and inactive rows, empty, 320 px, the stale alert, and
// each dialog state. No request. axe runs on each story in both themes.

const narrow = {
  parameters: {
    viewport: {
      options: {
        narrow320: {
          name: 'Narrow (320 px)',
          styles: { width: '320px', height: '640px' },
          type: 'mobile',
        },
      },
    },
  },
  globals: { viewport: { value: 'narrow320' } },
};

const refused = (code: string, status = 409) => new ApiError({ status, code });

const ready = (categories = MIXED_CATEGORIES): CategoriesState => ({
  status: 'ready',
  categories,
});

function Shell({ children }: { children: ReactNode }) {
  return (
    <StoryAuth session={EXPENSES_SESSION}>
      <StoryRouter
        path="/expenses/categories"
        routes={[{ path: '*', element: <main>{children}</main> }]}
      />
    </StoryAuth>
  );
}

const meta = {
  title: 'Finance/Categories',
  component: CategoriesView,
  args: {
    state: ready(),
    onAdd: fn(),
    onRename: fn(),
    onSetActive: fn(() => Promise.resolve()),
  },
  render: (args) => (
    <Shell>
      <CategoriesView {...args} />
    </Shell>
  ),
} satisfies Meta<typeof CategoriesView>;

export default meta;
type Story = StoryObj<typeof meta>;

/** The five seeded categories, each with its catalog label and badge. */
export const SeededOnly: Story = {
  args: { state: ready(SEEDED_CATEGORIES) },
  play: async ({ canvas }) => {
    const table = within(canvas.getByRole('table'));
    await expect(
      table.getByText(i18n.t('finance.categoryLabels.rent')),
    ).toBeVisible();
    await expect(
      table.getAllByText(i18n.t('finance.categories.default')),
    ).toHaveLength(5);
  },
};

/** Seeded, renamed, custom and inactive rows. */
export const Mixed: Story = {};

export const Loading: Story = { args: { state: { status: 'loading' } } };

export const Empty: Story = { args: { state: ready([]) } };

export const LoadFailed: Story = {
  args: {
    state: {
      status: 'error',
      error: refused('database_unavailable', 400),
      onRetry: fn(),
    },
  },
};

/** #458 C2: a refetch failed; the rows may be out of date. */
export const RefreshFailed: Story = {
  args: {
    state: {
      ...ready(),
      refreshError: refused('database_unavailable', 400),
      onRefresh: fn(),
    } as CategoriesState,
    announcement: i18n.t('finance.categories.goneNotReloaded'),
  },
  play: async ({ canvas }) => {
    await expect(canvas.getByRole('alert')).toHaveTextContent(
      i18n.t('finance.categories.stale'),
    );
    await expect(
      canvas.getByRole('button', { name: i18n.t('finance.reloadList') }),
    ).toBeVisible();
  },
};

export const Narrow: Story = { ...narrow };

export const DeactivateConfirm: Story = {
  args: { initialToggle: CAT_RENT },
};

export const DeactivateRefused: Story = {
  args: {
    initialToggle: CAT_RENT,
    initialToggleError: refused('permission_denied', 403),
  },
};

const dialogArgs = {
  open: true,
  onSubmit: fn(() => Promise.resolve('saved' as const)),
  onReload: fn(() => Promise.resolve(true)),
  onClose: fn(),
};

export const AddDialog: Story = {
  render: () => (
    <Shell>
      <CategoryDialog {...dialogArgs} category={null} />
    </Shell>
  ),
};

/** A taken name, with the reactivation hint. */
export const AddExists: Story = {
  render: () => (
    <Shell>
      <CategoryDialog
        {...dialogArgs}
        category={null}
        initialValues={{ name: 'Cafetería' }}
        initialErrors={{ name: 'errors:expense_category_exists' }}
      />
    </Shell>
  ),
};

export const AddTextInvalid: Story = {
  render: () => (
    <Shell>
      <CategoryDialog
        {...dialogArgs}
        category={null}
        initialValues={{ name: 'Algo' }}
        initialErrors={{ name: 'errors:text_invalid' }}
      />
    </Shell>
  ),
};

/** D2: a lost create retried into `…_exists`. */
export const AddMaybeSaved: Story = {
  render: () => (
    <Shell>
      <CategoryDialog
        {...dialogArgs}
        category={null}
        initialValues={{ name: 'Viáticos' }}
        initialErrors={{ name: 'errors:expense_category_exists' }}
        initialNotice="maybeSavedNotReloaded"
      />
    </Shell>
  ),
};

/** A keyed row: the both-languages warning. */
export const RenameDefault: Story = {
  render: () => (
    <Shell>
      <CategoryDialog {...dialogArgs} category={CAT_RENT} />
    </Shell>
  ),
};

export const RenameCustom: Story = {
  render: () => (
    <Shell>
      <CategoryDialog {...dialogArgs} category={CAT_CUSTOM} />
    </Shell>
  ),
};

/** B5: someone else changed it; the newer version replaced the form. */
export const RenameConflictReloaded: Story = {
  render: () => (
    <Shell>
      <CategoryDialog
        {...dialogArgs}
        category={CAT_CUSTOM}
        initialNotice="conflictReloaded"
      />
    </Shell>
  ),
};

export const RenameGone: Story = {
  render: () => (
    <Shell>
      <CategoryDialog
        {...dialogArgs}
        category={CAT_CUSTOM}
        initialNotice="goneNotReloaded"
      />
    </Shell>
  ),
};

export const DialogNarrow: Story = {
  ...narrow,
  render: () => (
    <Shell>
      <CategoryDialog {...dialogArgs} category={CAT_RENT} />
    </Shell>
  ),
};
