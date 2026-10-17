import type { Meta, StoryObj } from '@storybook/react-vite';
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
