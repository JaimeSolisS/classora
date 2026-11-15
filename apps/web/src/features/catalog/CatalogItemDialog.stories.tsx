import type { Meta, StoryObj } from '@storybook/react-vite';
import { expect, fn, waitFor, within } from 'storybook/test';
import { ApiError } from '@/app/api';
import { i18n } from '@/i18n';
import { CatalogItemDialog } from './CatalogItemDialog';
import { SECUNDARIA } from './fixtures';

// The level dialog (#32 H3, H4, J4): create, edit of an active level, the
// education_level_has_active_subjects alert and name_taken on the name
// field. No request: every call is a stub. The dialog is portalled to
// <body>, outside the story canvas.

const rejectWith = (code: string) =>
  fn(() =>
    Promise.reject(new ApiError({ code, status: 409, requestId: 'req-1' })),
  );

const ACTIVE_LEVEL = {
  name: SECUNDARIA.name,
  sortOrder: SECUNDARIA.sort_order,
  isActive: true,
  version: SECUNDARIA.version,
};

const meta = {
  title: 'Catalog/LevelDialog',
  component: CatalogItemDialog,
  args: {
    open: true,
    title: i18n.t('catalog.levels.add'),
    withPosition: true,
    item: null,
    onSubmit: fn(() => Promise.resolve()),
    onSetActive: fn(() => Promise.resolve()),
    onReload: fn(() => Promise.resolve()),
    onClose: fn(),
  },
} satisfies Meta<typeof CatalogItemDialog>;

export default meta;
type Story = StoryObj<typeof meta>;

type Context = Parameters<NonNullable<Story['play']>>[0];

const body = (context: Context) =>
  within(context.canvasElement.ownerDocument.body);

export const Create: Story = {
  play: async (context) => {
    const page = body(context);
    const dialog = await page.findByRole('dialog', {
      name: i18n.t('catalog.levels.add'),
    });
    await expect(
      within(dialog).getByLabelText(i18n.t('catalog.levels.name')),
    ).toHaveValue('');
    await expect(
      within(dialog).getByLabelText(i18n.t('catalog.levels.position')),
    ).toHaveAccessibleDescription(i18n.t('catalog.levels.positionHelp'));
    await expect(
      within(dialog).queryByRole('button', {
        name: i18n.t('catalog.actions.deactivate'),
      }),
    ).toBeNull();
  },
};

export const EditActiveLevel: Story = {
  args: { title: i18n.t('catalog.levels.edit'), item: ACTIVE_LEVEL },
  play: async (context) => {
    const page = body(context);
    const dialog = await page.findByRole('dialog', {
      name: i18n.t('catalog.levels.edit'),
    });
    await expect(
      within(dialog).getByLabelText(i18n.t('catalog.levels.name')),
    ).toHaveValue('Secundaria');
    await expect(
      within(dialog).getByLabelText(i18n.t('catalog.levels.position')),
    ).toHaveValue('20');
    await expect(
      within(dialog).getByRole('button', {
        name: i18n.t('catalog.actions.deactivate'),
      }),
    ).toHaveAccessibleDescription(i18n.t('catalog.deactivateHelp'));
  },
};

export const HasActiveSubjects: Story = {
  args: {
    title: i18n.t('catalog.levels.edit'),
    item: ACTIVE_LEVEL,
    onSetActive: rejectWith('education_level_has_active_subjects'),
  },
  play: async (context) => {
    const page = body(context);
    const dialog = await page.findByRole('dialog');
    await context.userEvent.click(
      within(dialog).getByRole('button', {
        name: i18n.t('catalog.actions.deactivate'),
      }),
    );
    await expect(await within(dialog).findByRole('alert')).toHaveTextContent(
      i18n.t('errors:education_level_has_active_subjects'),
    );
    await expect(context.args.onClose).not.toHaveBeenCalled();
  },
};

export const NameTaken: Story = {
  args: { onSubmit: rejectWith('name_taken') },
  play: async (context) => {
    const page = body(context);
    const dialog = await page.findByRole('dialog');
    const name = within(dialog).getByLabelText(i18n.t('catalog.levels.name'));
    await context.userEvent.type(name, 'Primaria');
    await context.userEvent.click(
      within(dialog).getByRole('button', {
        name: i18n.t('catalog.actions.save'),
      }),
    );
    await waitFor(() =>
      expect(name).toHaveAccessibleDescription(i18n.t('errors:name_taken')),
    );
    await expect(name).toHaveAttribute('aria-invalid', 'true');
    await expect(within(dialog).queryByRole('alert')).toBeNull();
  },
};
