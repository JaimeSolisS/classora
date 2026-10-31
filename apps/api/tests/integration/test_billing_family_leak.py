"""The family billing leak walk (#102 E, F; issue AC "IDOR test confirms no
cross-family access" and "balances match the ledger"; spec §6, W4.6;
ADR-0006 E4; ADR-0007 D17).

**The world** (:class:`World`, built once per module, through the real
routes where they exist): families **A** and **B**, each with an account.
Family A: a minor and an **adult** student (both with a login, no
``guardian_academic_access`` consent), guardian A (``family_guardians``,
linked to both), a package purchase with two discount lines and a consumed
unit (the minor), a package for the adult, manual charges for both, a
manual adjustment with a reason, a due-date change with a reason, a payment
with a note, a reversed payment, a refunded payment (each with a receipt).
Family B: a charge, a payment and a package, guardian B. Also: guardian X
(in B's ``family_guardians``, linked to A's minor by ``student_guardians``
only), an inactive guardian profile in A, a tutor of A's minor, an Academic
Coordinator, and the three ``billing.view`` roles.

**The canaries:** every staff-entered text (payment note, adjustment,
due-date-change, reversal and refund reasons) is a unique string that must
never reach a non-staff body (:attr:`World.staff_text`); every
family-A-only value (charge descriptions and ids, payment references and
ids, receipt numbers, package ids) must never reach a family-B caller, and
vice versa (:attr:`World.only`).

**The walk** (:func:`family_billing_routes`): every ``GET`` route of
``build_app(include_dev_routes=True)`` whose path starts with
:data:`PREFIX`, plus ``/api/v1/families/own``, so a route added later is
walked without editing this file. Its path parameters come from
:data:`PATH_PARAMS`; **an unmapped parameter fails**
(``test_every_path_parameter_is_mapped``). A route the matrix gates behind a
permission (``access = "permission"``: the due-date history) is staff-only:
every non-staff caller gets ``403 permission_denied`` there. The receipt
PDF is walked like the others, but for allowed callers only its status and
``Content-Type`` are checked.

**Extending it** (a new family-facing billing GET): map any new path
parameter in :data:`PATH_PARAMS` (and fill its ids in :class:`Ids`); new
staff-entered text → a canary in :func:`build_world`.
"""

import re
import uuid
from collections.abc import Iterator
from dataclasses import dataclass, field
from datetime import timedelta
from decimal import Decimal
from itertools import product
from typing import Any, Final

import pytest
from fastapi.routing import APIRoute
from fastapi.testclient import TestClient
from httpx import Response
from sqlalchemy.ext.asyncio import AsyncSession

from app.main import build_app
from app.modules.families.models import Family, Guardian, Student
from app.modules.identity.permissions import (
    ACADEMIC_COORDINATOR,
    ADMINISTRATOR,
    FINANCE_STAFF,
    GUARDIAN,
    OWNER,
    STUDENT,
    TUTOR,
)
from tests.authz.assertions import assert_same_not_found
from tests.authz.harness import api_route_contexts, load_matrix
from tests.conftest import FrozenClock, frozen_clock
from tests.factories.base import persist
from tests.factories.billing import assign_discount, persist_discount
from tests.factories.families import (
    ADULT_BIRTHDATE,
    FamilyFactory,
    GuardianFactory,
    add_to_family,
    link_guardian,
    login_consent,
    persist_student,
    student_login,
)
from tests.integration import corrections_support as cs
from tests.integration import family_support as fs
from tests.integration import package_support as pks
from tests.integration import payment_support as ps
from tests.integration.auth_support import (
    CreatedUser,
    SetCookie,
    auth_client,
    create_user,
)
from tests.integration.catalog_support import get, post
from tests.integration.conftest import run_sql
from tests.integration.invitation_support import sign_in
from tests.integration.lifecycle_support import code_of, in_uow
from tests.integration.schedule_support import stored
from tests.integration.session_request_support import MINOR_BIRTHDATE

PREFIX: Final = "/api/v1/families/{family_id}/billing-account"
OWN: Final = "/api/v1/families/own"
MISSING: Final = "00000000-0000-4000-8000-000000000102"
TODAY = ps.TODAY
SOLD = TODAY - timedelta(days=9)
PDF = "application/pdf"
STAFF_ONLY_KEYS: Final = frozenset({"note", "reason", "withdrawal_id"})
"""Plus every ``*_by_user_id`` key (#102 D1)."""


def _canary(label: str) -> str:
    return f"{label}-{uuid.uuid4().hex[:12]}"


# --- The world ----------------------------------------------------------------------


@dataclass(frozen=True)
class Ids:
    """The values of a family's path parameters."""

    family: str
    charges: list[str] = field(default_factory=list)
    payments: list[str] = field(default_factory=list)
    receipts: list[str] = field(default_factory=list)
    packages: list[str] = field(default_factory=list)


@dataclass
class World:
    a: Ids
    b: Ids
    minor: uuid.UUID
    adult: uuid.UUID
    users: dict[str, CreatedUser]
    guardian_ids: dict[str, uuid.UUID]
    staff_text: set[str]
    """Never in a non-staff body; in some staff body (positive control)."""
    only: dict[str, set[str]]
    """``"a"``/``"b"`` → values that must never reach the other family."""
    listed: dict[str, set[str]]
    """Route template → family-A values guardian A must see there."""
    owner_cookie: SetCookie | None = None


def build_world(url: str, client: TestClient) -> World:
    users = {
        name: create_user(url, roles=(GUARDIAN,), display_name=f"Fuga {name}")
        for name in ("guardian_a", "guardian_b", "guardian_x", "guardian_off")
    }
    users["minor"] = create_user(url, roles=(STUDENT,))
    users["adult"] = create_user(url, roles=(STUDENT,))
    users["tutor"] = create_user(url, roles=(TUTOR,))
    users["coordinator"] = create_user(url, roles=(ACADEMIC_COORDINATOR,))
    for role in (ADMINISTRATOR, FINANCE_STAFF):
        users[role] = create_user(url, roles=(role,))
    owner = cs.staff(url, client, (OWNER,))
    users[OWNER] = owner.user

    async def _families(session: AsyncSession) -> tuple[Any, ...]:
        family_a: Family = await persist(session, FamilyFactory)
        family_b: Family = await persist(session, FamilyFactory)
        minor = await persist_student(session, contacts=0, birthdate=MINOR_BIRTHDATE)
        adult = await persist_student(session, contacts=0, birthdate=ADULT_BIRTHDATE)
        b_student = await persist_student(session, contacts=0)

        async def guardian(name: str, *, active: bool = True) -> Guardian:
            made: Guardian = await persist(
                session, GuardianFactory, user_id=users[name].id, is_active=active
            )
            return made

        ga = await guardian("guardian_a")
        gb = await guardian("guardian_b")
        gx = await guardian("guardian_x")
        off = await guardian("guardian_off", active=False)
        await add_to_family(session, family_a, minor, adult, ga, off)
        await add_to_family(session, family_b, b_student, gb, gx)
        await link_guardian(session, minor, ga)
        await link_guardian(session, adult, ga)
        await link_guardian(session, minor, gx)
        # The minor's login rests on guardian A's student_login consent; the
        # adult's needs none. No guardian_academic_access anywhere (E4).
        await login_consent(session, minor, ga, recorded_by=users["guardian_a"].id)
        await student_login(session, minor, users["minor"].id)
        await student_login(session, adult, users["adult"].id)
        for name, discount_kind, values in (
            ("Beca", "scholarship", {"amount": "50.00"}),
            ("Descuento", "discount", {"percent": "10"}),
        ):
            discount = await persist_discount(
                session,
                kind=discount_kind,
                audience="assigned",
                name=_canary(name),
                eligibility={"charge_kinds": ["package_purchase"]},
                **values,
            )
            await assign_discount(session, discount.id, student_id=minor.id)
        ids = {"a": ga.id, "b": gb.id, "x": gx.id, "off": off.id}
        return family_a.id, family_b.id, minor.id, adult.id, b_student.id, ids

    a, b, minor, adult, b_student, guardian_ids = in_uow(url, _families)

    # Packages (the minor's has two discount lines and a consumed unit).
    minor_package = pks.sell(url, minor, pks.product(url, amount="1000.00"), SOLD)
    adult_package = pks.sell(url, adult, pks.product(url, amount="400.00"), SOLD)
    b_package = pks.sell(url, b_student, pks.product(url, amount="300.00"), SOLD)
    session = stored(
        url,
        pks.local(SOLD + timedelta(days=2)),
        students=(minor,),
        tutor_user_id=users["tutor"].id,
    )
    assert pks.consume(url, uuid.UUID(session), minor).outcome == "consumed"

    # Manual charges, an adjustment and a due-date change (staff text).
    descriptions = {"a": [_canary("Libro A"), _canary("Uniforme A")]}
    descriptions["b"] = [_canary("Libro B")]
    staff_text = {
        key: _canary(key) for key in ("NOTA", "AJUSTE", "VENCE", "REVERSO", "REEMB")
    }
    minor_charge = cs.created(
        cs.charge_post(
            client,
            owner.cookie,
            a,
            cs.charge_body(minor, "300.00", description=descriptions["a"][0]),
        )
    )["charge"]["id"]
    adult_charge = cs.created(
        cs.charge_post(
            client,
            owner.cookie,
            a,
            cs.charge_body(adult, "200.00", description=descriptions["a"][1]),
        )
    )["charge"]["id"]
    cs.created(
        cs.charge_post(
            client,
            owner.cookie,
            b,
            cs.charge_body(b_student, "75.00", description=descriptions["b"][0]),
        )
    )
    cs.created(
        cs.adjust(client, owner.cookie, a, minor_charge, "-20.00", staff_text["AJUSTE"])
    )
    changed = post(
        client,
        owner.cookie,
        ps.account_path(a, f"/charges/{adult_charge}/due-date-changes"),
        {
            "due_date": (TODAY + timedelta(days=14)).isoformat(),
            "reason": staff_text["VENCE"],
        },
    )
    assert changed.status_code == 201, changed.text

    # Payments (each with a receipt): a note, a reversal, a refund.
    references = {"a": [_canary("REF-A") for _ in range(3)], "b": [_canary("REF-B")]}

    def pay(family: uuid.UUID, amount: str, reference: str, **extra: Any) -> str:
        response = ps.pay(
            client,
            owner.cookie,
            family,
            ps.payment_body(amount, reference=reference, payer_name="Pagador", **extra),
        )
        assert response.status_code == 201, response.text
        payment_id: str = response.json()["payment"]["id"]
        return payment_id

    pay(a, "100.00", references["a"][0], note=staff_text["NOTA"])
    reversed_ = pay(a, "80.00", references["a"][1])
    cs.created(cs.reverse(client, owner.cookie, a, reversed_, staff_text["REVERSO"]))
    refunded = pay(a, "150.00", references["a"][2], mode="none")
    cs.created(
        cs.refund(
            client,
            owner.cookie,
            a,
            refunded,
            cs.refund_body("40.00", reason=staff_text["REEMB"]),
        )
    )
    pay(b, "20.00", references["b"][0])

    world = World(
        a=Ids(str(a)),
        b=Ids(str(b)),
        minor=minor,
        adult=adult,
        users=users,
        guardian_ids=guardian_ids,
        staff_text=set(staff_text.values()),
        only={},
        listed={},
        owner_cookie=owner.cookie,
    )
    world.a = _ids_of(client, owner.cookie, a)
    world.b = _ids_of(client, owner.cookie, b)
    assert len(world.a.charges) == 4 and len(world.a.payments) == 3
    assert {str(minor_package), str(adult_package)} == set(world.a.packages)
    assert world.b.packages == [str(b_package)]
    numbers = {
        key: _receipt_numbers(client, owner.cookie, ids)
        for key, ids in (("a", world.a), ("b", world.b))
    }
    for key, ids in (("a", world.a), ("b", world.b)):
        world.only[key] = (
            set(descriptions[key])
            | set(references[key])
            | numbers[key]
            | set(ids.charges)
            | set(ids.payments)
            | set(ids.receipts)
            | set(ids.packages)
        )
    world.listed = {
        f"{PREFIX}/charges": set(descriptions["a"]) | set(world.a.charges),
        f"{PREFIX}/payments": set(references["a"]) | numbers["a"],
        f"{PREFIX}/packages": set(world.a.packages),
    }
    return world


def _all_items(
    client: TestClient, cookie: SetCookie, path: str, **params: Any
) -> list[Any]:
    response = get(client, cookie, path, limit=100, **params)
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["total"] == len(body["items"])
    items: list[Any] = body["items"]
    return items


def _ids_of(client: TestClient, cookie: SetCookie, family: uuid.UUID) -> Ids:
    payments = _all_items(client, cookie, ps.account_path(family, "/payments"))
    return Ids(
        family=str(family),
        charges=[
            c["id"]
            for c in _all_items(client, cookie, ps.account_path(family, "/charges"))
        ],
        payments=[p["id"] for p in payments],
        receipts=[p["receipt"]["id"] for p in payments if p["receipt"] is not None],
        packages=[
            p["id"]
            for p in _all_items(client, cookie, ps.account_path(family, "/packages"))
        ],
    )


def _receipt_numbers(client: TestClient, cookie: SetCookie, ids: Ids) -> set[str]:
    payments = _all_items(client, cookie, ps.account_path(ids.family, "/payments"))
    return {p["receipt"]["number"] for p in payments if p["receipt"] is not None}


# --- The route map ------------------------------------------------------------------


PATH_PARAMS: Final = {
    "family_id": lambda ids: [ids.family],
    "charge_id": lambda ids: list(ids.charges),
    "payment_id": lambda ids: list(ids.payments),
    "receipt_id": lambda ids: list(ids.receipts),
    "package_id": lambda ids: list(ids.packages),
}
"""Every path parameter of a walked route → its values for one family. A
missing name fails ``test_every_path_parameter_is_mapped``."""

_PARAM = re.compile(r"{(\w+)(?::\w+)?}")


@dataclass(frozen=True)
class BillingRoute:
    path: str
    params: tuple[str, ...]
    staff_only: bool
    """The matrix gates it behind a permission (403 for everyone else)."""

    @property
    def sub_params(self) -> tuple[str, ...]:
        return tuple(name for name in self.params if name != "family_id")


def family_billing_routes() -> list[BillingRoute]:
    """Every family-facing billing ``GET`` route (the module docstring),
    sorted by path."""
    entries = {(entry.method, entry.path): entry for entry in load_matrix()}
    found = []
    for context in api_route_contexts(build_app(include_dev_routes=True)):
        if not isinstance(context.original_route, APIRoute):
            continue
        if "GET" not in (context.methods or ()):
            continue
        path = str(context.path)
        if not (path == OWN or path.startswith(PREFIX)):
            continue
        entry = entries[("GET", path)]
        found.append(
            BillingRoute(
                path, tuple(_PARAM.findall(path)), entry.access == "permission"
            )
        )
    return sorted(found, key=lambda route: route.path)


def _fill(template: str, values: dict[str, str]) -> str:
    path = template
    for name, value in values.items():
        path = re.sub(r"{" + name + r"(?::\w+)?}", value, path)
    return path


def calls(route: BillingRoute, family: str, subs: Ids) -> Iterator[tuple[str, str]]:
    """``(path, the same path under an unknown family)`` for every
    combination of ``subs``' values of the route's sub-parameters."""
    choices = [PATH_PARAMS[name](subs) for name in route.sub_params]
    for values in product(*choices):
        named = dict(zip(route.sub_params, values, strict=True))
        yield (
            _fill(route.path, {"family_id": family, **named}),
            _fill(route.path, {"family_id": MISSING, **named}),
        )


# --- Fixtures -----------------------------------------------------------------------


@pytest.fixture(scope="module")
def frozen() -> Iterator[FrozenClock]:
    with frozen_clock(ps.NOW) as clock:
        yield clock


@pytest.fixture(scope="module")
def client(module_seeded_url: str, frozen: FrozenClock) -> Iterator[TestClient]:
    with auth_client(module_seeded_url) as test_client:
        yield test_client


@pytest.fixture(scope="module")
def world(module_seeded_url: str, client: TestClient) -> World:
    return build_world(module_seeded_url, client)


@pytest.fixture(scope="module")
def cookies(client: TestClient, world: World) -> dict[str, SetCookie]:
    return {name: sign_in(client, user.username) for name, user in world.users.items()}


STAFF: Final = (OWNER, ADMINISTRATOR, FINANCE_STAFF)
DENIED: Final = (
    "guardian_b",
    "guardian_x",
    "guardian_off",
    "minor",
    "adult",
    "tutor",
    "coordinator",
)
"""Everyone who must not see family A (B's guardians too)."""


def _staff_keys(value: Any, where: str = "") -> Iterator[str]:
    """Every staff-only key (D1) with a non-null value."""
    if isinstance(value, dict):
        for key, inner in value.items():
            here = f"{where}.{key}"
            if (key in STAFF_ONLY_KEYS or key.endswith("_by_user_id")) and (
                inner is not None
            ):
                yield here
            yield from _staff_keys(inner, here)
    elif isinstance(value, list):
        for index, inner in enumerate(value):
            yield from _staff_keys(inner, f"{where}[{index}]")


def _contains(response: Response, values: set[str]) -> set[str]:
    if response.headers.get("content-type", "").startswith(PDF):
        return set()
    text = response.text
    return {value for value in values if value in text}


def _ok(response: Response, path: str) -> None:
    assert response.status_code == 200, (path, response.text[:300])
    if path.endswith("/pdf"):
        assert response.headers["content-type"] == PDF, path


# --- The tests ----------------------------------------------------------------------


def test_every_path_parameter_is_mapped():
    routes = family_billing_routes()
    unmapped = {
        f"{route.path}: unmapped parameter {name!r}"
        for route in routes
        for name in route.params
        if name not in PATH_PARAMS
    }
    assert not unmapped, (
        "Add these to PATH_PARAMS in tests/integration/test_billing_family_leak.py"
        f" (see its docstring): {sorted(unmapped)}"
    )
    paths = {route.path: route for route in routes}
    assert {
        OWN,
        PREFIX,
        f"{PREFIX}/charges",
        f"{PREFIX}/charges/{{charge_id}}",
        f"{PREFIX}/ledger",
        f"{PREFIX}/payments",
        f"{PREFIX}/payments/{{payment_id}}",
        f"{PREFIX}/receipts/{{receipt_id}}/pdf",
        f"{PREFIX}/packages",
        f"{PREFIX}/packages/{{package_id}}",
    } <= set(paths)
    assert [p for p, route in paths.items() if route.staff_only] == [
        f"{PREFIX}/charges/{{charge_id}}/due-date-changes"
    ]


def test_guardian_a_reads_every_route_without_staff_text(client, world, cookies):
    cookie = cookies["guardian_a"]
    seen: dict[str, str] = {}
    for route in family_billing_routes():
        for path, _ in calls(route, world.a.family, world.a):
            response = get(client, cookie, path)
            if route.staff_only:
                assert code_of(response) == (403, "permission_denied"), path
                continue
            _ok(response, path)
            assert not _contains(response, world.staff_text), path
            assert not _contains(response, world.only["b"]), path
            if not path.endswith("/pdf"):
                assert list(_staff_keys(response.json())) == [], path
            seen[path] = "" if path.endswith("/pdf") else response.text
    # Positive control: the family's own values are on the routes listing
    # them.
    for template, values in world.listed.items():
        text = seen[_fill(template, {"family_id": world.a.family})]
        assert {v for v in values if v not in text} == set(), template
    assert len(seen) > 15


def test_another_family_is_the_unknown_family_404(client, world, cookies):
    for name in DENIED:
        cookie = cookies[name]
        for route in family_billing_routes():
            if route.path == OWN:
                continue
            for path, missing in calls(route, world.a.family, world.a):
                response = get(client, cookie, path)
                assert not _contains(response, world.staff_text | world.only["a"])
                if route.staff_only:
                    assert code_of(response) == (403, "permission_denied"), (
                        name,
                        path,
                    )
                    continue
                assert_same_not_found(response, get(client, cookie, missing))


def test_foreign_sub_ids_under_the_own_family_are_404(client, world, cookies):
    """Guardian B with B's family id and A's charge, payment, receipt and
    package ids (and A with B's)."""
    for name, own, foreign, other in (
        ("guardian_b", world.b, world.a, "a"),
        ("guardian_a", world.a, world.b, "b"),
    ):
        cookie = cookies[name]
        for route in family_billing_routes():
            if not route.sub_params or route.staff_only:
                continue
            for path, missing in calls(route, own.family, foreign):
                response = get(client, cookie, path)
                assert_same_not_found(response, get(client, cookie, missing))
                assert not _contains(response, world.only[other])


def test_guardian_b_sees_only_family_b(client, world, cookies):
    for name in ("guardian_b", "guardian_x"):
        cookie = cookies[name]
        for route in family_billing_routes():
            if route.staff_only or route.path == OWN:
                continue
            for path, _ in calls(route, world.b.family, world.b):
                response = get(client, cookie, path)
                _ok(response, path)
                assert not _contains(response, world.only["a"] | world.staff_text)
                if not path.endswith("/pdf"):
                    assert list(_staff_keys(response.json())) == [], path


def test_own_family_names_only_the_callers_family(client, world, cookies):
    expected = {
        "guardian_a": world.a.family,
        "guardian_b": world.b.family,
        "guardian_x": world.b.family,
    }
    for name, cookie in cookies.items():
        response = get(client, cookie, OWN)
        assert response.status_code == 200, response.text
        family = response.json()["family"]
        assert (family and family["id"]) == expected.get(name), name


@pytest.mark.parametrize("role", STAFF)
def test_staff_see_both_families_with_the_staff_text(client, world, cookies, role):
    cookie = cookies[role]
    texts: list[str] = []
    for ids in (world.a, world.b):
        for route in family_billing_routes():
            if route.path == OWN:
                continue
            for path, _ in calls(route, ids.family, ids):
                response = get(client, cookie, path)
                _ok(response, path)
                if not path.endswith("/pdf"):
                    texts.append(response.text)
    everything = "\n".join(texts)
    # The redaction, not missing data, hides them from families.
    assert {text for text in world.staff_text if text not in everything} == set()
    assert f'"posted_by_user_id":"{world.users[OWNER].id}"' in everything
    assert f'"recorded_by_user_id":"{world.users[OWNER].id}"' in everything


def _snapshot(client: TestClient, cookie: SetCookie, world: World) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for route in family_billing_routes():
        if route.staff_only:
            continue
        for path, _ in calls(route, world.a.family, world.a):
            response = get(client, cookie, path)
            _ok(response, path)
            out[path] = (
                response.headers["content-type"]
                if path.endswith("/pdf")
                else response.json()
            )
    return out


def test_the_adult_childs_billing_needs_no_academic_consent(
    module_seeded_url, client, world, cookies
):
    """ADR-0007 D17: billing follows the family. Guardian A sees the adult's
    charges, ledger rows and packages with no consent, and granting then
    withdrawing the academic consent changes nothing."""
    url = module_seeded_url
    active = run_sql(
        url,
        "SELECT id FROM consent_records WHERE student_id = :s"
        " AND scope = 'guardian_academic_access' AND revoked_at IS NULL",
        {"s": world.adult},
    )
    assert active == []
    cookie = cookies["guardian_a"]
    adult = str(world.adult)
    for suffix in ("/charges", "/ledger", "/packages"):
        response = get(
            client, cookie, ps.account_path(world.a.family, suffix), student_id=adult
        )
        assert response.status_code == 200, response.text
        assert response.json()["items"], suffix
    before = _snapshot(client, cookie, world)

    async def _grant(session: AsyncSession) -> uuid.UUID:
        student = await session.get(Student, world.adult)
        guardian = await session.get(Guardian, world.guardian_ids["a"])
        assert student is not None and guardian is not None
        record = await login_consent(
            session,
            student,
            guardian,
            recorded_by=world.users["guardian_a"].id,
            scope="guardian_academic_access",
        )
        return record.id

    consent = in_uow(url, _grant)
    with_consent = _snapshot(client, cookie, world)
    run_sql(
        url,
        "UPDATE consent_records SET revoked_at = now(), revoked_by_user_id = :u,"
        " revocation_cause = 'revoked'"
        " WHERE id = :id",
        {"u": world.users["guardian_a"].id, "id": consent},
    )
    withdrawn = _snapshot(client, cookie, world)

    assert with_consent == before
    assert withdrawn == before


def _paths(world: World, ids: Ids) -> Iterator[tuple[str, str]]:
    for route in family_billing_routes():
        if route.staff_only or route.path == OWN:
            continue
        yield from calls(route, ids.family, ids)


def test_moves_apply_on_the_next_request(module_seeded_url, client, world):
    """E5: a guardian removed from a family loses it on the next request and
    sees the new one once added; a student who moves leaves their charges
    and packages on the old account."""
    url = module_seeded_url
    owner = world.owner_cookie
    assert owner is not None
    old_family, [student] = ps.family(url)
    new_family, _ = ps.family(url, 0)
    user = create_user(url, roles=(GUARDIAN,))

    async def _guardian(session: AsyncSession) -> uuid.UUID:
        made: Guardian = await persist(session, GuardianFactory, user_id=user.id)
        return made.id

    guardian = in_uow(url, _guardian)
    fs.added(client, owner, old_family, "guardians", guardian)
    [old_charge] = ps.charges(url, (student, "90.00", TODAY))
    old_package = pks.sell(url, student, pks.product(url), SOLD)
    cookie = sign_in(client, user.username)
    old = _ids_of(client, owner, old_family)
    # The manual charge and the package purchase charge.
    assert str(old_charge) in old.charges and len(old.charges) == 2
    assert old.packages == [str(old_package)]

    for path, _ in _paths(world, old):
        _ok(get(client, cookie, path), path)
    assert get(client, cookie, OWN).json()["family"]["id"] == str(old_family)

    removed = fs.remove(client, owner, old_family, "guardians", guardian)
    assert removed.status_code == 204, removed.text
    for path, missing in _paths(world, old):
        assert_same_not_found(get(client, cookie, path), get(client, cookie, missing))
    assert get(client, cookie, OWN).json() == {"family": None}

    fs.added(client, owner, new_family, "guardians", guardian)
    assert get(client, cookie, OWN).json()["family"]["id"] == str(new_family)
    for route in family_billing_routes():
        if route.staff_only or route.path == OWN:
            continue
        # The new family (no account yet): its own reads answer, the old
        # family's charge and package under it don't.
        for path, missing in calls(route, str(new_family), old):
            response = get(client, cookie, path)
            if route.sub_params:
                assert_same_not_found(response, get(client, cookie, missing))
            else:
                _ok(response, path)
    for path, missing in _paths(world, old):
        assert_same_not_found(get(client, cookie, path), get(client, cookie, missing))

    # The student moves: old rows stay on the old account.
    assert fs.remove(client, owner, old_family, "students", student).status_code == 204
    fs.added(client, owner, new_family, "students", student)
    [new_charge] = ps.charges(url, (student, "60.00", TODAY))
    new_package = pks.sell(url, student, pks.product(url), TODAY)
    new = _ids_of(client, owner, new_family)
    assert str(new_charge) in new.charges and len(new.charges) == 2
    assert new.packages == [str(new_package)]
    assert set(new.charges).isdisjoint(old.charges)
    assert _ids_of(client, owner, old_family) == old
    # The guardian (now in the new family) sees only the new rows.
    assert _ids_of(client, cookie, new_family) == new


# --- F. Balances match the ledger ---------------------------------------------------


def _money(value: str) -> Decimal:
    return Decimal(value)


def _ledger(client: TestClient, cookie: SetCookie, family: str) -> list[Any]:
    rows: list[Any] = []
    cursor: str | None = None
    for _ in range(50):
        params: dict[str, Any] = {"limit": 100}
        if cursor is not None:
            params["cursor"] = cursor
        response = get(client, cookie, ps.account_path(family, "/ledger"), **params)
        assert response.status_code == 200, response.text
        rows.extend(response.json()["items"])
        cursor = response.json()["next_cursor"]
        if cursor is None:
            return rows
    raise AssertionError("the ledger never ended")


@pytest.mark.parametrize("name", ["guardian_a", "guardian_b"])
def test_balances_match_the_guardians_own_reads(client, world, cookies, name):
    """F1: recomputed from guardian-visible responses only."""
    cookie = cookies[name]
    family = world.a.family if name == "guardian_a" else world.b.family
    summary = get(client, cookie, ps.account_path(family)).json()
    charges = _all_items(client, cookie, ps.account_path(family, "/charges"))
    payments = _all_items(client, cookie, ps.account_path(family, "/payments"))
    ledger = _ledger(client, cookie, family)

    def open_total(rows: list[Any], status: str | None = None) -> Decimal:
        return sum(
            (
                _money(c["open"])
                for c in rows
                if c["status"] != "void" and (status is None or c["status"] == status)
            ),
            Decimal("0"),
        )

    assert _money(summary["outstanding"]) == open_total(charges)
    for status in ("pending", "partially_paid", "overdue"):
        assert _money(summary[status]) == open_total(charges, status), status
    credit = sum(
        (_money(p["unallocated_amount"]) for p in payments if not p["is_reversed"]),
        Decimal("0"),
    )
    assert _money(summary["account_credit"]) == credit
    assert _money(summary["net_balance"]) == sum(
        (_money(e["balance_effect"]) for e in ledger), Decimal("0")
    )
    assert _money(summary["net_balance"]) == open_total(charges) - credit
    assert summary["students"]
    for row in summary["students"]:
        own = _all_items(
            client,
            cookie,
            ps.account_path(family, "/charges"),
            student_id=row["student"]["id"],
        )
        assert _money(row["outstanding"]) == open_total(own), row
        for status in ("pending", "partially_paid", "overdue"):
            assert _money(row[status]) == open_total(own, status), (row, status)
    if name == "guardian_a":
        assert _money(summary["outstanding"]) > 0
        assert _money(summary["account_credit"]) > 0
