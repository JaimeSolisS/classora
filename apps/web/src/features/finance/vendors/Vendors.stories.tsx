import type { Meta, StoryObj } from '@storybook/react-vite';
import type { ReactNode } from 'react';
import { expect, fn } from 'storybook/test';
import { ApiError } from '@/app/api';
import { i18n } from '@/i18n';
import { StoryAuth } from '@/test/StoryAuth';
import { StoryRouter } from '@/test/StoryRouter';
import {
  EXPENSES_SESSION,
  VENDOR_LONG,
  VENDOR_PAPER,
  VENDORS,
  vendorPage,
} from '../fixtures';
import { VendorDialog } from './VendorDialog';
import { VendorsView, type VendorsState } from './VendorsView';

// The vendors tab and its dialogs (M7-11b F2): empty, mixed, a search with no
// match, 320 px with a long contact, the stale alert, and each dialog state.
// No request. axe runs on each story in both themes.

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

const ready = (items = VENDORS, total = items.length): VendorsState => ({
  status: 'ready',
  page: vendorPage(items, { total }),
});

function Shell({ children }: { children: ReactNode }) {
  return (
    <StoryAuth session={EXPENSES_SESSION}>
      <StoryRouter
        path="/expenses/vendors"
        routes={[{ path: '*', element: <main>{children}</main> }]}
      />
    </StoryAuth>
  );
}

const meta = {
  title: 'Finance/Vendors',
  component: VendorsView,
  args: {
    state: ready(),
    search: '',
    onSearch: fn(),
    stateFilter: 'all',
    onStateFilter: fn(),
    filtered: false,
    onPage: fn(),
    onAdd: fn(),
    onEdit: fn(),
    onSetActive: fn(() => Promise.resolve()),
  },
  render: (args) => (
    <Shell>
      <VendorsView {...args} />
    </Shell>
  ),
} satisfies Meta<typeof VendorsView>;

export default meta;
type Story = StoryObj<typeof meta>;

/** Active and inactive vendors, with and without a contact, two pages. */
export const Mixed: Story = { args: { state: ready(VENDORS, 30) } };

export const Empty: Story = {
  args: { state: ready([]) },
  play: async ({ canvas }) => {
    await expect(
      canvas.getByText(i18n.t('finance.vendors.empty')),
    ).toBeVisible();
  },
};

export const SearchNoMatch: Story = {
  args: { state: ready([]), search: 'zzz', filtered: true },
  play: async ({ canvas }) => {
    await expect(
      canvas.getByText(i18n.t('finance.vendors.noMatch')),
    ).toBeVisible();
  },
};

export const Loading: Story = { args: { state: { status: 'loading' } } };

/** #458 C2: a refetch failed; the rows may be out of date. */
export const RefreshFailed: Story = {
  args: {
    state: {
      ...ready(),
      refreshError: refused('database_unavailable', 400),
      onRefresh: fn(),
    } as VendorsState,
  },
  play: async ({ canvas }) => {
    await expect(canvas.getByRole('alert')).toHaveTextContent(
      i18n.t('finance.vendors.stale'),
    );
  },
};

/** A long name and contact wrap; nothing scrolls sideways. */
export const Narrow: Story = {
  ...narrow,
  args: { state: ready([VENDOR_LONG, VENDOR_PAPER]) },
};

export const DeactivateConfirm: Story = {
  args: { initialToggle: VENDOR_PAPER },
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
      <VendorDialog {...dialogArgs} vendor={null} />
    </Shell>
  ),
};

export const EditDialog: Story = {
  render: () => (
    <Shell>
      <VendorDialog {...dialogArgs} vendor={VENDOR_PAPER} />
    </Shell>
  ),
};

/** A taken name, with the reactivation hint. */
export const AddExists: Story = {
  render: () => (
    <Shell>
      <VendorDialog
        {...dialogArgs}
        vendor={null}
        initialValues={{ name: 'Agua Purificada Demo', contact: '' }}
        initialErrors={{ name: 'errors:vendor_exists' }}
      />
    </Shell>
  ),
};

export const ContactTooLong: Story = {
  render: () => (
    <Shell>
      <VendorDialog
        {...dialogArgs}
        vendor={null}
        initialValues={{ name: 'Proveedor', contact: 'x'.repeat(501) }}
        initialErrors={{ contact: 'finance.validation.contactTooLong' }}
      />
    </Shell>
  ),
};

/** D2: a lost create retried into `vendor_exists`. */
export const AddMaybeSaved: Story = {
  render: () => (
    <Shell>
      <VendorDialog
        {...dialogArgs}
        vendor={null}
        initialValues={{ name: 'Nuevo', contact: '' }}
        initialErrors={{ name: 'errors:vendor_exists' }}
        initialNotice="maybeSavedReloaded"
      />
    </Shell>
  ),
};

export const EditGone: Story = {
  render: () => (
    <Shell>
      <VendorDialog
        {...dialogArgs}
        vendor={VENDOR_PAPER}
        initialNotice="goneReloaded"
      />
    </Shell>
  ),
};

export const DialogNarrow: Story = {
  ...narrow,
  render: () => (
    <Shell>
      <VendorDialog {...dialogArgs} vendor={VENDOR_LONG} />
    </Shell>
  ),
};
