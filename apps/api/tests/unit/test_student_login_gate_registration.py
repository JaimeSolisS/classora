"""The student-login gate is registered by every composition root (#35 B1).

``identity`` defines the ``StudentLoginGate`` hook and can't import
``families`` (ADR-0002); the API app factory, the CLI dispatcher and the
worker entry point register the families gate. Without one, activating a
student fails closed (the integration tests cover that path).
"""

from collections.abc import Iterator

import pytest

from app import cli, worker
from app.main import build_app
from app.modules.families import service as families_service
from app.modules.families.logins import FamiliesStudentLoginGate
from app.modules.identity.service import (
    StudentLoginGate,
    register_student_login_gate,
    registered_student_login_gate,
)


@pytest.fixture
def unregistered(student_login_gate: None) -> Iterator[None]:
    register_student_login_gate(None)
    assert registered_student_login_gate() is None
    yield


def test_the_families_gate_implements_the_protocol():
    gate: StudentLoginGate = families_service.STUDENT_LOGIN_GATE
    assert isinstance(gate, FamiliesStudentLoginGate)


def test_the_api_app_factory_registers_the_gate(unregistered):
    build_app()

    assert registered_student_login_gate() is families_service.STUDENT_LOGIN_GATE


def test_the_cli_registers_the_gate(unregistered):
    # A usage error still runs the registration (it comes first).
    with pytest.raises(SystemExit):
        cli.main(["bootstrap-owner"])

    assert registered_student_login_gate() is families_service.STUDENT_LOGIN_GATE


def test_the_worker_registers_the_gate(unregistered):
    # No settings in the test environment: the entry point exits with 1 after
    # registering the gate.
    assert worker.main([]) == 1

    assert registered_student_login_gate() is families_service.STUDENT_LOGIN_GATE
