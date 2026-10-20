import {
  sameWindows,
  type WeeklyWindow,
} from '@/components/weekly-windows/windows';
import type { Classroom, ClassroomCreate, ClassroomUpdate } from './api';

// Pure helpers of the classrooms page (#39 D4): the request bodies from the
// form values. An update sends the loaded `version` plus only the fields
// that changed (`availability` as the full list when the set of windows
// changed); null means nothing changed (no request).

/** The API's capacity bounds (models.CLASSROOM_CAPACITY_MIN/MAX). */
export const CAPACITY_MIN = 1;
export const CAPACITY_MAX = 500;

export interface ClassroomFormValues {
  /** Trimmed name. */
  name: string;
  capacity: number;
  /** The windows in row order. */
  availability: WeeklyWindow[];
}

export function classroomCreateBody(
  values: ClassroomFormValues,
): ClassroomCreate {
  return {
    name: values.name,
    capacity: values.capacity,
    availability: values.availability,
  };
}

export function classroomUpdateBody(
  classroom: Classroom,
  values: ClassroomFormValues,
): ClassroomUpdate | null {
  const body: ClassroomUpdate = { version: classroom.version };
  if (values.name !== classroom.name) {
    body.name = values.name;
  }
  if (values.capacity !== classroom.capacity) {
    body.capacity = values.capacity;
  }
  if (!sameWindows(values.availability, classroom.availability)) {
    body.availability = values.availability;
  }
  return Object.keys(body).length > 1 ? body : null;
}
