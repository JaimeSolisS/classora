import { useTranslation } from 'react-i18next';
import type { RoomUtilization, RoomUtilizationRow } from './api';
import { useKpiFormatters } from './format';
import { Percent } from './Figures';
import { KpiSection, NOTE_CLASS } from './KpiSection';
import { KpiTable } from './KpiTable';
import { useRoomUtilization } from './queries';
import { kpiState, type KpiState } from './state';

// K4 "Room utilization" (M8-09b C4; _docs/kpis.md K4): the active rooms by
// name, with available and used minutes and the percent ("—" with "No
// schedule available" when nothing was available), and a total row. A
// percent over 100 comes from classes booked outside the room's hours, and
// a note says so.

const L = 'reports.kpis.rooms';

type Figures = Omit<RoomUtilizationRow, 'room'>;

const over100 = (row: Figures) =>
  row.utilization_percent !== null && Number(row.utilization_percent) > 100;

function isEmptyRooms(report: RoomUtilization): boolean {
  return (
    report.total.available_minutes === 0 && report.total.used_minutes === 0
  );
}

export function RoomUtilizationView({
  state,
}: {
  state: KpiState<RoomUtilization>;
}) {
  const { t } = useTranslation();
  const format = useKpiFormatters();
  const cells = (row: Figures) => [
    format.count(row.available_minutes),
    format.count(row.used_minutes),
    <Percent
      key="percent"
      value={row.utilization_percent}
      missing={t(`${L}.noSchedule`)}
    />,
  ];
  return (
    <KpiSection
      kpi="room-utilization"
      title={t(`${L}.title`)}
      state={state}
      isEmpty={isEmptyRooms}
    >
      {(report) => (
        <div className="flex min-w-0 flex-col gap-2">
          <KpiTable
            data="room-utilization"
            caption={t(`${L}.caption`)}
            columns={[
              t(`${L}.columns.room`),
              t(`${L}.columns.available`),
              t(`${L}.columns.used`),
              t(`${L}.columns.percent`),
            ]}
            rows={report.rooms.map((row) => ({
              key: row.room.id,
              header: row.room.name,
              cells: cells(row),
            }))}
            total={{
              key: 'total',
              header: t(`${L}.total`),
              cells: cells(report.total),
            }}
          />
          {[...report.rooms, report.total].some(over100) ? (
            <p className={NOTE_CLASS} data-kpi-note="over-100">
              {t(`${L}.over100`)}
            </p>
          ) : null}
        </div>
      )}
    </KpiSection>
  );
}

/** K4 with its own query (#127 mounts it as a widget). */
export function RoomUtilizationSection({
  from,
  to,
}: {
  from: string;
  to: string;
}) {
  const query = useRoomUtilization({ from, to });
  return <RoomUtilizationView state={kpiState(query)} />;
}
