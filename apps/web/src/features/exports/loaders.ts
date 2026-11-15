import {
  fetchRoomOptions,
  searchStudentOptions,
  searchTutorOptions,
} from '@/features/calendar/api';
import { fetchActiveLevels } from '@/features/students/api';

// The request form's option loaders (M8-07b B1): the calendar's tutor,
// student and room options and the students page's education levels.
// Stories pass stubs.

export interface ExportFormLoaders {
  searchTutors: typeof searchTutorOptions;
  searchStudents: typeof searchStudentOptions;
  fetchRooms: typeof fetchRoomOptions;
  fetchLevels: typeof fetchActiveLevels;
}

export const API_FORM_LOADERS: ExportFormLoaders = {
  searchTutors: searchTutorOptions,
  searchStudents: searchStudentOptions,
  fetchRooms: fetchRoomOptions,
  fetchLevels: fetchActiveLevels,
};
