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
- ``people``, ``catalog``, ``staff``, ``settings``: this issue's modules
  (HF-01b…HF-01f append theirs to :data:`SIMULATION_MODULES`);
- ``__main__``: the command.
"""

from typing import Final

from tests.factories.simulation.catalog import CatalogModule
from tests.factories.simulation.people import PeopleModule
from tests.factories.simulation.settings import SettingsModule
from tests.factories.simulation.staff import StaffModule
from tests.factories.simulation.timeline import SimulationModule

SIMULATION_MODULES: Final[tuple[SimulationModule, ...]] = (
    PeopleModule(),
    CatalogModule(),
    StaffModule(),
    SettingsModule(),
)
"""Every module, in order; each day runs them in this order."""
