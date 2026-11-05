import type { Meta, StoryObj } from '@storybook/react-vite';
import { expect, fn, within } from 'storybook/test';
import { i18n } from '@/i18n';
import { StoryRouter } from '@/test/StoryRouter';
import { DetailPanel } from './DetailPanel';
import { PENDING_REGISTRATION, SESSION_CLOSEOUT } from './fixtures';

// The detail panel of one approval request (M8-01b G2): an inbox-decided
// type (registration), an own-page type (closeout), a closed item, the
// assignment controls with their options, the not-found state and a phone.
// No request: the handlers are stubs. The panel is portalled to <body>.

const ME = {
  id: '00000000-0000-4000-8000-000000000001',
  display_name: 'Demo Owner',
};
const LAURA = {
  id: '00000000-0000-4000-8000-0000000000d7',
  display_name: 'Laura Méndez',
};

const meta = {
  title: 'Approvals/DetailPanel',
  component: DetailPanel,
  args: {
    open: true,
    state: { status: 'ready', item: PENDING_REGISTRATION },
    notice: null,
    currentUserId: ME.id,
    options: { status: 'ready', items: [ME, LAURA] },
    assigning: false,
    onAssign: fn(),
    onDecide: fn(),
    onClose: fn(),
    onCloseFocus: fn(),
  },
  render: (args) => (
    <StoryRouter routes={[{ path: '/', element: <DetailPanel {...args} /> }]} />
  ),
} satisfies Meta<typeof DetailPanel>;

export default meta;
type Story = StoryObj<typeof meta>;

type Context = Parameters<NonNullable<Story['play']>>[0];

const body = (context: Context) =>
  within(context.canvasElement.ownerDocument.body);

/** An inbox-decided type: its fields, assignment and decisions. */
export const Registration: Story = {
  play: async (context) => {
    const dialog = await body(context).findByRole('dialog', {
      name: i18n.t('approvals.panel.title', {
        type: i18n.t('approvals.types.identity_registration'),
        summary: i18n.t('approvals.summary.registration', {
          name: 'Rosa Díaz',
          username: 'rosa.diaz',
          role: i18n.t('roles.guardian'),
        }),
      }),
    });
    await expect(within(dialog).getByText('rosa.diaz')).toBeVisible();
    await context.userEvent.click(
      within(dialog).getByRole('button', {
        name: i18n.t('approvals.actions.approve'),
      }),
    );
    await expect(context.args.onDecide).toHaveBeenCalledWith(
      PENDING_REGISTRATION,
      'approve',
    );
  },
};

/** An own-page type: the link to its page, no decisions. */
export const SessionCloseout: Story = {
  args: { state: { status: 'ready', item: SESSION_CLOSEOUT } },
  play: async (context) => {
    const dialog = await body(context).findByRole('dialog');
    await expect(
      within(dialog).getByRole('link', {
        name: i18n.t('approvals.panel.reviewOnPage'),
      }),
    ).toHaveAttribute(
      'href',
      '/schedule/sessions/00000000-0000-4000-8000-0000000000f2#closeout',
    );
    await expect(
      within(dialog).queryByRole('button', {
        name: i18n.t('approvals.actions.approve'),
      }),
    ).toBeNull();
  },
};

/** A closed item: the resolution, the assignee read-only. */
export const Closed: Story = {
  args: {
    state: {
      status: 'ready',
      item: {
        ...PENDING_REGISTRATION,
        status: 'rejected',
        assignee: LAURA,
        resolved_at: '2026-10-19T17:05:00Z',
        resolved_by: ME,
        reason: 'Familia desconocida.',
        allowed_actions: [],
        version: 3,
      },
    },
  },
  play: async (context) => {
    const dialog = await body(context).findByRole('dialog');
    await expect(within(dialog).getByText('Laura Méndez')).toBeVisible();
    await expect(
      within(dialog).getByText('Familia desconocida.'),
    ).toBeVisible();
    await expect(within(dialog).queryByRole('combobox')).toBeNull();
  },
};

/** Assigned to the viewer: no "Assign to me"; reassign to someone else. */
export const AssignmentControls: Story = {
  args: {
    state: {
      status: 'ready',
      item: { ...PENDING_REGISTRATION, assignee: ME, version: 2 },
    },
  },
  play: async (context) => {
    const dialog = await body(context).findByRole('dialog');
    await expect(
      within(dialog).queryByRole('button', {
        name: i18n.t('approvals.assign.toMe'),
      }),
    ).toBeNull();
    const select = within(dialog).getByRole('combobox', {
      name: i18n.t('approvals.assign.toLabel'),
    });
    await expect(select).toHaveValue(ME.id);
    await context.userEvent.selectOptions(select, LAURA.id);
    await context.userEvent.click(
      within(dialog).getByRole('button', {
        name: i18n.t('approvals.assign.submit'),
      }),
    );
    await expect(context.args.onAssign).toHaveBeenCalledWith(LAURA.id);
    await context.userEvent.click(
      within(dialog).getByRole('button', {
        name: i18n.t('approvals.assign.remove'),
      }),
    );
    await expect(context.args.onAssign).toHaveBeenCalledWith(null);
  },
};

/** The request is gone or not reviewable (B1). */
export const NotFound: Story = {
  args: { state: { status: 'notFound' } },
  play: async (context) => {
    const dialog = await body(context).findByRole('dialog', {
      name: i18n.t('approvals.panel.genericTitle'),
    });
    await expect(within(dialog).getByRole('alert')).toHaveTextContent(
      i18n.t('approvals.panel.notFound'),
    );
    await expect(
      within(dialog).getByRole('button', {
        name: i18n.t('approvals.panel.close'),
      }),
    ).toBeVisible();
  },
};

/** A phone (320 px): full screen, nothing scrolls sideways (D3). */
export const Mobile: Story = {
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
  play: async (context) => {
    const dialog = await body(context).findByRole('dialog');
    await expect(dialog.scrollWidth).toBeLessThanOrEqual(dialog.clientWidth);
  },
};
