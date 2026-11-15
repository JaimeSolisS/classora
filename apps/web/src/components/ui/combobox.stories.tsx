import type { Meta, StoryObj } from '@storybook/react-vite';
import { useState } from 'react';
import { expect, waitFor, within } from 'storybook/test';
import { matchesSearch } from '@/lib/search';
import { StoryQueryClient } from '@/test/StoryQueryClient';
import { Button } from './button';
import {
  Combobox,
  type ComboboxProps,
  type ComboboxSelection,
  type OptionsPage,
} from './combobox';
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from './dialog';

// The type-ahead combobox (#545 D3): the list on click, accent-insensitive
// filtering as you type, the keyboard, every state, add mode, a dialog and
// a 320 px viewport. Fixture copy is English; the component's own texts
// come from the catalogs (es by default, the locale toolbar). Axe runs in
// light and dark (the storybook projects). No request leaves the story.

interface Student {
  id: string;
  name: string;
}

const STUDENTS: Student[] = [
  { id: 's1', name: 'Ana Ñúñez' },
  { id: 's2', name: 'Bruno Díaz' },
  { id: 's3', name: 'Carla Méndez' },
  { id: 's4', name: 'José Pérez' },
  { id: 's5', name: 'Lucía Gómez' },
  { id: 's6', name: 'Mateo Ríos Luna' },
];

type Load = ComboboxProps<Student>['load'];

/** Every student: local filtering. */
const loadAll: Load = () =>
  Promise.resolve({ items: STUDENTS, total: STUDENTS.length });

/** A large source: the first page holds 6 of 312, `q` goes to the server. */
const loadLarge: Load = (q) => {
  if (q === null) {
    return Promise.resolve({ items: STUDENTS, total: 312 });
  }
  const items = STUDENTS.filter((student) => matchesSearch(student.name, q));
  return Promise.resolve({ items, total: items.length });
};

function Field({
  load = loadAll,
  initial = null,
  ...rest
}: Partial<ComboboxProps<Student>> & { initial?: ComboboxSelection | null }) {
  const [selected, setSelected] = useState(initial);
  return (
    <StoryQueryClient>
      <div className="max-w-md">
        <Combobox<Student>
          label="Student"
          help="Click the field or type part of a name."
          queryKey={['story', 'combobox']}
          load={load}
          optionId={(student) => student.id}
          optionLabel={(student) => student.name}
          selected={selected}
          onSelect={(student) =>
            setSelected({ id: student.id, label: student.name })
          }
          {...rest}
        />
      </div>
    </StoryQueryClient>
  );
}

const meta = {
  title: 'UI/Combobox',
  component: Field,
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
} satisfies Meta<typeof Field>;

export default meta;
type Story = StoryObj<typeof meta>;

/** The portalled list, outside the story canvas. */
function page(canvasElement: HTMLElement) {
  return within(canvasElement.ownerDocument.body);
}

export const Closed: Story = {
  play: async ({ canvas }) => {
    const field = canvas.getByRole('combobox', { name: 'Student' });
    await expect(field).toHaveAttribute('aria-expanded', 'false');
  },
};

export const OpenList: Story = {
  play: async ({ canvas, canvasElement, userEvent }) => {
    const field = canvas.getByRole('combobox', { name: 'Student' });
    await userEvent.click(field);
    const list = await page(canvasElement).findByRole('listbox', {
      name: 'Student',
    });
    await waitFor(() =>
      expect(within(list).getAllByRole('option')).toHaveLength(6),
    );
    await expect(field).toHaveAttribute('aria-expanded', 'true');
  },
};

export const FilteredAccentInsensitive: Story = {
  play: async ({ canvas, canvasElement, userEvent }) => {
    const field = canvas.getByRole('combobox', { name: 'Student' });
    await userEvent.click(field);
    await userEvent.type(field, 'jose');
    const list = await page(canvasElement).findByRole('listbox');
    await waitFor(() =>
      expect(
        within(list)
          .getAllByRole('option')
          .map((option) => option.textContent),
      ).toEqual(['José Pérez']),
    );
    await userEvent.keyboard('{ArrowDown}');
    await expect(field).toHaveAttribute('aria-activedescendant');
    await userEvent.keyboard('{Enter}');
    await expect(field).toHaveValue('José Pérez');
    await expect(field).toHaveAttribute('aria-expanded', 'false');
  },
};

export const NoResults: Story = {
  play: async ({ canvas, canvasElement, userEvent }) => {
    const field = canvas.getByRole('combobox', { name: 'Student' });
    await userEvent.click(field);
    await userEvent.type(field, 'zzz');
    await page(canvasElement).findByText(/zzz/, {
      selector: 'p:not([role])',
    });
    await userEvent.keyboard('{Escape}');
    await expect(field).toHaveAttribute('aria-expanded', 'false');
    await userEvent.keyboard('{Escape}');
    await expect(field).toHaveValue('');
  },
};

export const EmptySource: Story = {
  args: {
    load: () => Promise.resolve({ items: [], total: 0 }),
    emptyText: 'There are no students to choose from.',
  },
  play: async ({ canvas, canvasElement, userEvent }) => {
    await userEvent.click(canvas.getByRole('combobox', { name: 'Student' }));
    await page(canvasElement).findByText(
      'There are no students to choose from.',
      { selector: 'p:not([role])' },
    );
  },
};

export const Loading: Story = {
  args: { load: () => new Promise<OptionsPage<Student>>(() => {}) },
  play: async ({ canvas, canvasElement, userEvent }) => {
    await userEvent.click(canvas.getByRole('combobox', { name: 'Student' }));
    const list = await page(canvasElement).findByRole('listbox');
    await expect(list).toHaveAttribute('aria-busy', 'true');
  },
};

export const ErrorWithRetry: Story = {
  args: { load: () => Promise.reject(new Error('down')) },
  play: async ({ canvas, userEvent }) => {
    const field = canvas.getByRole('combobox', { name: 'Student' });
    await userEvent.click(field);
    const retry = await canvas.findByRole('button', {
      name: /Reintentar|Try again/,
    });
    await userEvent.keyboard('{Escape}');
    await userEvent.tab();
    await expect(retry).toHaveFocus();
  },
};

export const DisabledOption: Story = {
  args: {
    optionDisabled: (student) => student.id === 's1',
    optionNote: (student) =>
      student.id === 's1' ? 'already enrolled' : undefined,
  },
  play: async ({ canvas, userEvent }) => {
    const field = canvas.getByRole('combobox', { name: 'Student' });
    await userEvent.click(field);
    await userEvent.keyboard('{ArrowDown}');
    await waitFor(() =>
      expect(
        field.ownerDocument.getElementById(
          field.getAttribute('aria-activedescendant') ?? '',
        )?.textContent,
      ).toBe('Bruno Díaz'),
    );
  },
};

/** Accounts (#548): the label carries state and role words. */
const ACCOUNTS: Student[] = [
  { id: 'a1', name: 'ana.p · Ana Pérez · Active · Tutor' },
  { id: 'a2', name: 'beto.r · Beto Ruiz · Archived · Guardian (archived)' },
  { id: 'a3', name: 'carla.m · Carla Méndez · Pending · Student' },
];
const USERNAMES: Record<string, string> = {
  a1: 'ana.p Ana Pérez',
  a2: 'beto.r Beto Ruiz',
  a3: 'carla.m Carla Méndez',
};

export const SearchTextDiffersFromLabel: Story = {
  args: {
    label: 'Account',
    help: 'Type part of a username or name.',
    load: () => Promise.resolve({ items: ACCOUNTS, total: ACCOUNTS.length }),
    optionSearchText: (account) => USERNAMES[account.id] ?? account.name,
    optionDisabled: (account) => account.id === 'a2',
  },
  play: async ({ canvas, canvasElement, userEvent }) => {
    const field = canvas.getByRole('combobox', { name: 'Account' });
    await userEvent.click(field);
    // A role word in the label doesn't match: the search text has none.
    await userEvent.type(field, 'tutor');
    const list = await page(canvasElement).findByRole('listbox', {
      name: 'Account',
    });
    await waitFor(() =>
      expect(within(list).queryAllByRole('option')).toHaveLength(0),
    );
    await userEvent.clear(field);
    await userEvent.type(field, 'carla.m');
    await waitFor(() =>
      expect(
        within(list)
          .getAllByRole('option')
          .map((option) => option.textContent),
      ).toEqual(['carla.m · Carla Méndez · Pending · Student']),
    );
    await userEvent.keyboard('{ArrowDown}{Enter}');
    await expect(field).toHaveValue(
      'carla.m · Carla Méndez · Pending · Student',
    );
  },
};

export const Truncated: Story = {
  args: { load: loadLarge },
  play: async ({ canvas, canvasElement, userEvent }) => {
    const field = canvas.getByRole('combobox', { name: 'Student' });
    await userEvent.click(field);
    await page(canvasElement).findByText(/312/, { selector: 'p:not([role])' });
    await userEvent.type(field, 'mateo');
    const list = await page(canvasElement).findByRole('listbox');
    await waitFor(() =>
      expect(
        within(list)
          .getAllByRole('option')
          .map((option) => option.textContent),
      ).toEqual(['Mateo Ríos Luna']),
    );
  },
};

function AddModeField() {
  const [chosen, setChosen] = useState<Student[]>([]);
  return (
    <StoryQueryClient>
      <div className="flex max-w-md flex-col gap-3">
        <Combobox<Student>
          mode="add"
          label="Add a student"
          queryKey={['story', 'combobox', 'add']}
          load={loadAll}
          optionId={(student) => student.id}
          optionLabel={(student) => student.name}
          optionDisabled={(student) =>
            chosen.some((item) => item.id === student.id)
          }
          optionNote={(student) =>
            chosen.some((item) => item.id === student.id) ? 'added' : undefined
          }
          selected={null}
          onSelect={(student) => setChosen((list) => [...list, student])}
        />
        <ul aria-label="Chosen students" className="flex flex-wrap gap-2">
          {chosen.map((student) => (
            <li
              key={student.id}
              className="rounded-md border border-border px-2 py-0.5 text-sm"
            >
              {student.name}
            </li>
          ))}
        </ul>
      </div>
    </StoryQueryClient>
  );
}

export const AddMode: Story = {
  render: () => <AddModeField />,
  play: async ({ canvas, userEvent }) => {
    const field = canvas.getByRole('combobox', { name: 'Add a student' });
    await userEvent.click(field);
    await userEvent.type(field, 'ana');
    await userEvent.keyboard('{ArrowDown}{Enter}');
    await expect(field).toHaveValue('');
    await expect(field).toHaveFocus();
    await expect(field).toHaveAttribute('aria-expanded', 'true');
    const chips = canvas.getByRole('list', { name: 'Chosen students' });
    await expect(within(chips).getByText('Ana Ñúñez')).toBeVisible();
  },
};

function DialogField() {
  const [open, setOpen] = useState(true);
  return (
    <StoryQueryClient>
      <Button onClick={() => setOpen(true)}>Enroll student</Button>
      <Dialog open={open} onOpenChange={setOpen}>
        <DialogContent className="max-h-[calc(100dvh-2rem)] overflow-y-auto">
          <DialogHeader>
            <DialogTitle>Enroll student</DialogTitle>
            <DialogDescription>
              The list opens over the dialog and is never clipped.
            </DialogDescription>
          </DialogHeader>
          <Field />
        </DialogContent>
      </Dialog>
    </StoryQueryClient>
  );
}

export const InDialog: Story = {
  render: () => <DialogField />,
  play: async ({ canvasElement, userEvent }) => {
    const body = page(canvasElement);
    const dialog = await body.findByRole('dialog', { name: 'Enroll student' });
    const field = within(dialog).getByRole('combobox', { name: 'Student' });
    await userEvent.click(field);
    const list = await body.findByRole('listbox', { name: 'Student' });
    await waitFor(() =>
      expect(within(list).getAllByRole('option')).toHaveLength(6),
    );
    await userEvent.click(within(list).getByText('Carla Méndez'));
    await expect(field).toHaveValue('Carla Méndez');
    // The dialog stays open: the list is part of it.
    await expect(dialog).toBeVisible();
  },
};

export const Narrow320: Story = {
  globals: { viewport: { value: 'narrow320' } },
  play: async ({ canvas, canvasElement, userEvent }) => {
    await userEvent.click(canvas.getByRole('combobox', { name: 'Student' }));
    await page(canvasElement).findByRole('listbox');
    const root = canvasElement.ownerDocument.documentElement;
    await expect(root.scrollWidth).toBeLessThanOrEqual(root.clientWidth);
  },
};
