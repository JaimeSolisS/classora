"""Dashboard layout request and response shapes (#127 C1, C3)."""

from typing import Annotated

from pydantic import AfterValidator, BaseModel, ConfigDict, Field, ValidationError
from pydantic_core import InitErrorDetails, PydanticCustomError

from app.modules.reporting.dashboard.models import MAX_STORED_WIDGETS
from app.modules.reporting.dashboard.registry import WidgetCode


class DashboardWidget(BaseModel):
    """One widget of the layout, in display order."""

    model_config = ConfigDict(extra="forbid")

    widget: WidgetCode
    visible: bool = Field(description="Whether the dashboard shows it.")


def _no_duplicates(widgets: list[DashboardWidget]) -> list[DashboardWidget]:
    seen: set[str] = set()
    errors: list[InitErrorDetails] = []
    for index, entry in enumerate(widgets):
        if entry.widget in seen:
            errors.append(
                InitErrorDetails(
                    type=PydanticCustomError(
                        "widget_duplicate", "List each widget only once."
                    ),
                    loc=(index, "widget"),
                    input=entry.widget,
                )
            )
        seen.add(entry.widget)
    if errors:
        raise ValidationError.from_exception_data("DashboardWidgets", errors)
    return widgets


class DashboardLayoutUpdate(BaseModel):
    """``PUT /api/v1/reports/dashboard/layout`` (#127 C3)."""

    model_config = ConfigDict(extra="forbid")

    widgets: Annotated[
        list[DashboardWidget],
        Field(min_length=1, max_length=MAX_STORED_WIDGETS),
        AfterValidator(_no_duplicates),
    ] = Field(
        description="The widgets in display order, each once, 1-20. Available"
        " widgets left out are appended, shown, at the end."
    )


class DashboardLayoutResponse(BaseModel):
    """The caller's effective dashboard layout (#127 C1): exactly the widgets
    available to them, each once, in their order. Never a KPI figure: each
    widget reads its own KPI route."""

    widgets: list[DashboardWidget]
    customized: bool = Field(
        description="Whether the caller saved a layout (false: the role default)."
    )
