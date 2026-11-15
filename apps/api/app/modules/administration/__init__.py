"""Administration: the audit log read API (architecture §4.1).

Audit events are captured by ``app.core.audit`` (shared infrastructure,
ISSUE-A3); this module owns reading them (``GET /api/v1/audit-events``).
"""
