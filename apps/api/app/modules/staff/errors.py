"""Staff errors (#43 D3: no code of its own).

Tutor profiles answer with the shared codes only: ``404 not_found``
(:class:`~app.core.authz.RecordNotFoundError`), ``403 permission_denied``
(:class:`~app.core.authz.PermissionDeniedError`) and ``409
version_conflict`` (:class:`~app.core.models.StaleVersionError`). Later staff
issues (#44, #45) add their typed errors here.
"""
