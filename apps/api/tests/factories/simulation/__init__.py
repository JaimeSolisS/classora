"""The simulated operations seed (#543 HF-01; ``make seed-data-spanish`` /
``make seed-data-english``): a deterministic tutoring center from 1 Sep to
5 Dec 2026, written through the real services on a clean local database.

Test-support code only (nothing under ``app/`` imports it), separate from
``make seed-demo`` (E1): its own accounts, plan and timeline. The package
name must not start with ``demo`` (``test_demo_guard.py`` matches recipe
lines containing ``python -m tests.factories.demo``).

- ``anchor``: the fixed timeline and :func:`~.anchor.sim_clock`;
- ``plan``: the pure plan; ``texts``: the language pools;
- ``timeline``: the driver, the module protocol and the clean check;
- ``people``, ``catalog``, ``staff``, ``settings``: HF-01's modules;
- ``timetable``: HF-01b's pure timetable and outcomes; ``schedule`` and
  ``courses``: HF-01b's modules (after ``settings``, so the scheduling
  policy is in place);
- ``coursework``: HF-01c's pure academic record; ``academics``: its module
  (after ``courses``; HF-01d…HF-01f append theirs to
  :data:`SIMULATION_MODULES`);
- ``__main__``: the command.
"""

from typing import Final

from tests.factories.simulation.academics import AcademicsModule
from tests.factories.simulation.catalog import CatalogModule
from tests.factories.simulation.courses import CoursesModule
from tests.factories.simulation.people import PeopleModule
from tests.factories.simulation.schedule import ScheduleModule
from tests.factories.simulation.settings import SettingsModule
from tests.factories.simulation.staff import StaffModule
from tests.factories.simulation.timeline import SimulationModule

SIMULATION_MODULES: Final[tuple[SimulationModule, ...]] = (
    PeopleModule(),
    CatalogModule(),
    StaffModule(),
    SettingsModule(),
    ScheduleModule(),
    CoursesModule(),
    AcademicsModule(),
)
"""Every module, in order; each day runs them in this order."""
