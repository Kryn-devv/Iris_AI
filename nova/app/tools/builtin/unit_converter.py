"""Unit converter tool for physical and mathematical conversions."""

from typing import Any, Dict
from nova.app.tools.base import BaseTool
from nova.app.core.security import PermissionLevel
from nova.app.schemas.tools import ToolParameterSchema


class UnitConverterTool(BaseTool):
    """Tool for converting units of length, weight, and temperature."""

    name = "unit_converter"
    description = "Converts physical values between units (e.g. fahrenheit to celsius, meters to feet, kg to lbs)."
    permission_level = PermissionLevel.LOW_RISK_ACTION
    input_schema = ToolParameterSchema(
        type="object",
        properties={
            "value": {
                "type": "number",
                "description": "Numeric value to convert.",
            },
            "from_unit": {
                "type": "string",
                "description": "Starting unit (e.g. 'fahrenheit', 'celsius', 'meters', 'feet', 'kg', 'lbs').",
            },
            "to_unit": {
                "type": "string",
                "description": "Target unit (e.g. 'celsius', 'fahrenheit', 'feet', 'meters', 'lbs', 'kg').",
            },
        },
        required=["value", "from_unit", "to_unit"],
    )

    async def _run(
        self,
        value: float = 0.0,
        from_unit: str = "",
        to_unit: str = "",
        **kwargs: Any,
    ) -> Dict[str, Any]:
        fu = from_unit.lower().strip()
        tu = to_unit.lower().strip()

        # Temperature
        if fu == "fahrenheit" and tu == "celsius":
            res = (value - 32.0) * (5.0 / 9.0)
        elif fu == "celsius" and tu == "fahrenheit":
            res = (value * (9.0 / 5.0)) + 32.0
        # Length
        elif fu in ("meters", "meter", "m") and tu in ("feet", "foot", "ft"):
            res = value * 3.28084
        elif fu in ("feet", "foot", "ft") and tu in ("meters", "meter", "m"):
            res = value / 3.28084
        # Weight
        elif fu in ("kg", "kilograms", "kilogram") and tu in ("lbs", "pounds", "pound"):
            res = value * 2.20462
        elif fu in ("lbs", "pounds", "pound") and tu in ("kg", "kilograms", "kilogram"):
            res = value / 2.20462
        else:
            raise ValueError(f"Unsupported unit conversion from '{from_unit}' to '{to_unit}'")

        res_rounded = round(res, 4)
        return {
            "value": value,
            "from_unit": fu,
            "to_unit": tu,
            "result": res_rounded,
            "formatted": f"{value} {from_unit} = {res_rounded} {to_unit}",
        }
