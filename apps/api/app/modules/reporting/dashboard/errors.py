"""Dashboard layout errors with stable codes (#127 C3, E6)."""

from app.core.errors import AppError


class DashboardWidgetUnavailableError(AppError):
    """``422 dashboard_widget_unavailable`` at ``["body", "widgets", N,
    "widget"]``: the caller doesn't pass that widget's KPI route permission,
    so it can't be on their dashboard."""

    status = 422
    code = "dashboard_widget_unavailable"
    detail = "A widget is not available to you."

    def __init__(self, index: int) -> None:
        super().__init__(
            errors=[
                {
                    "path": ["body", "widgets", index, "widget"],
                    "code": "dashboard_widget_unavailable",
                    "message": "This widget is not available to you.",
                }
            ]
        )
