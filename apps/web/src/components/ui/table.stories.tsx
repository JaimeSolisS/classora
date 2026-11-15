import type { Meta, StoryObj } from '@storybook/react-vite';
import { expect, within } from 'storybook/test';
import {
  Table,
  TableBody,
  TableCaption,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from './table';

const meta = {
  title: 'UI/Table',
  component: Table,
} satisfies Meta<typeof Table>;

export default meta;
type Story = StoryObj<typeof meta>;

const rows = [
  { group: 'Algebra I', tutor: 'Ana López', students: 8 },
  { group: 'Physics', tutor: 'Luis Pérez', students: 5 },
  { group: 'English B1', tutor: 'Sara Kim', students: 11 },
];

export const WithCaption: Story = {
  render: () => (
    <Table>
      <TableCaption>Groups this term</TableCaption>
      <TableHeader>
        <TableRow>
          <TableHead scope="col">Group</TableHead>
          <TableHead scope="col">Tutor</TableHead>
          <TableHead scope="col" className="text-right">
            Students
          </TableHead>
        </TableRow>
      </TableHeader>
      <TableBody>
        {rows.map((row) => (
          <TableRow key={row.group}>
            <TableCell>{row.group}</TableCell>
            <TableCell>{row.tutor}</TableCell>
            <TableCell className="text-right">{row.students}</TableCell>
          </TableRow>
        ))}
      </TableBody>
    </Table>
  ),
};

// Wider than its container, with no focusable cell (#234 QA D2): the
// scroll container takes the focus and is a region named like the table,
// so a keyboard user can scroll to the hidden columns (axe
// scrollable-region-focusable).
export const Overflowing: Story = {
  render: () => (
    <div className="w-60">
      <h2 id="overflowing-heading" className="font-medium">
        Withdrawn
      </h2>
      <Table aria-labelledby="overflowing-heading">
        <TableHeader>
          <TableRow>
            <TableHead scope="col">Group</TableHead>
            <TableHead scope="col">Tutor</TableHead>
            <TableHead scope="col">Withdrawn by</TableHead>
            <TableHead scope="col">Reason</TableHead>
          </TableRow>
        </TableHeader>
        <TableBody>
          {rows.map((row) => (
            <TableRow key={row.group}>
              <TableCell>{row.group}</TableCell>
              <TableCell>{row.tutor}</TableCell>
              <TableCell>{row.tutor}</TableCell>
              <TableCell>Moved to another city</TableCell>
            </TableRow>
          ))}
        </TableBody>
      </Table>
    </div>
  ),
  play: async ({ canvasElement }) => {
    const region = within(canvasElement).getByRole('region', {
      name: 'Withdrawn',
    });
    await expect(region).toHaveAttribute('tabindex', '0');
    await expect(region.scrollWidth).toBeGreaterThan(region.clientWidth);
  },
};

// A table that fits adds no tab stop and no region.
export const Fitting: Story = {
  render: () => (
    <Table aria-label="Groups this term">
      <TableHeader>
        <TableRow>
          <TableHead scope="col">Group</TableHead>
          <TableHead scope="col">Tutor</TableHead>
        </TableRow>
      </TableHeader>
      <TableBody>
        {rows.map((row) => (
          <TableRow key={row.group}>
            <TableCell>{row.group}</TableCell>
            <TableCell>{row.tutor}</TableCell>
          </TableRow>
        ))}
      </TableBody>
    </Table>
  ),
  play: async ({ canvasElement }) => {
    const container = canvasElement.querySelector(
      '[data-slot="table-container"]',
    );
    await expect(container).not.toHaveAttribute('tabindex');
    await expect(within(canvasElement).queryByRole('region')).toBeNull();
  },
};
