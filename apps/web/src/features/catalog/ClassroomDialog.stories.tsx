import type { Meta, StoryObj } from '@storybook/react-vite';
import { expect, fn, waitFor, within } from 'storybook/test';
import { ApiError } from '@/app/api';
import { i18n } from '@/i18n';
import { ClassroomDialog } from './ClassroomDialog';
import { BODEGA } from './fixtures';

// The classroom dialog (#39 D3, D6): add a classroom, edit one with
// windows, and name_taken on the name. No request: every call is a stub.
// The dialog is portalled to <body>.

const meta = {
  title: 'Catalog/ClassroomDialog',
  component: ClassroomDialog,
  args: {
    open: true,
    classroom: null,
    onSubmit: fn(() => Promise.resolve()),
    onSetActive: fn(() => Promise.resolve()),
    onReload: fn(() => Promise.resolve()),
    onClose: fn(),
  },
} satisfies Meta<typeof ClassroomDialog>;

export default meta;
type Story = StoryObj<typeof meta>;

type Context = Parameters<NonNullable<Story['play']>>[0];

const body = (context: Context) =>
  within(context.canvasElement.ownerDocument.body);

export const Create: Story = {
  play: async (context) => {
    const dialog = await body(context).findByRole('dialog', {
      name: i18n.t('catalog.classrooms.add'),
    });
    await expect(
      within(dialog).getByLabelText(i18n.t('catalog.classrooms.capacity')),
    ).toHaveAccessibleDescription(i18n.t('catalog.classrooms.capacityHelp'));
    await expect(
      within(dialog).getByText(i18n.t('weeklyWindows.empty')),
    ).toBeVisible();
  },
};

export const Edit: Story = {
  args: { classroom: BODEGA },
  play: async (context) => {
    const dialog = await body(context).findByRole('dialog', {
      name: i18n.t('catalog.classrooms.edit'),
    });
    await expect(
      within(dialog).getByLabelText(
        i18n.t('weeklyWindows.endsAtLabel', { number: 2 }),
      ),
    ).toHaveValue('14:00');
    await expect(
      within(dialog).getByRole('button', {
        name: i18n.t('catalog.actions.reactivate'),
      }),
    ).toBeVisible();
  },
};

export const NameTaken: Story = {
  args: {
    onSubmit: fn(() =>
      Promise.reject(
        new ApiError({ code: 'name_taken', status: 409, requestId: 'r' }),
      ),
    ),
  },
  play: async (context) => {
    const dialog = await body(context).findByRole('dialog');
    const name = within(dialog).getByLabelText(
      i18n.t('catalog.classrooms.name'),
    );
    await context.userEvent.type(name, 'Aula 1');
    await context.userEvent.type(
      within(dialog).getByLabelText(i18n.t('catalog.classrooms.capacity')),
      '12',
    );
    await context.userEvent.click(
      within(dialog).getByRole('button', {
        name: i18n.t('catalog.actions.save'),
      }),
    );
    await waitFor(() => expect(name).toHaveAttribute('aria-invalid', 'true'));
    await expect(name).toHaveAccessibleDescription(i18n.t('errors:name_taken'));
    await expect(context.args.onClose).not.toHaveBeenCalled();
  },
};
