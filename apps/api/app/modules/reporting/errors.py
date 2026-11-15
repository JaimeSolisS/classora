"""Financial report errors (#117 E2)."""

from typing import Literal

from app.core.errors import AppError

type RangeParameter = Literal["from", "to"]


class ReportRangeInvalidError(AppError):
    """``from > to``, ``to`` after the center's today, or a span over 366
    days (``to − from ≥ 366``): ``422 report_range_invalid`` at the offending
    query parameter."""

    status = 422
    code = "report_range_invalid"
    detail = "The report range is not valid."

    def __init__(self, parameter: RangeParameter, message: str) -> None:
        super().__init__(
            errors=[
                {
                    "path": ["query", parameter],
                    "code": "report_range_invalid",
                    "message": message,
                }
            ]
        )
