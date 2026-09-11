"""Unit converter tool for physical and mathematical conversions.

Every unit has a canonical name and a set of spellings people actually say —
"miles", "mi", "mile" — because a converter that knows "meters" but not
"metres", or "kg" but not "kilos", fails on exactly the phrasing it will get.
"""

from typing import Any, Dict, Optional, Tuple

from iris.app.tools.base import BaseTool, ToolError
from iris.app.core.security import PermissionLevel
from iris.app.schemas.tools import ToolParameterSchema

#: canonical unit -> (dimension, factor to the dimension's base unit)
_UNITS: Dict[str, Tuple[str, float]] = {
    # length, base metre
    "mm": ("length", 0.001), "cm": ("length", 0.01), "m": ("length", 1.0), "km": ("length", 1000.0),
    "in": ("length", 0.0254), "ft": ("length", 0.3048), "yd": ("length", 0.9144), "mi": ("length", 1609.344),
    # mass, base kilogram
    "mg": ("mass", 1e-6), "g": ("mass", 0.001), "kg": ("mass", 1.0), "t": ("mass", 1000.0),
    "oz": ("mass", 0.028349523125), "lb": ("mass", 0.45359237),
    # volume, base litre
    "ml": ("volume", 0.001), "l": ("volume", 1.0), "gal": ("volume", 3.785411784),
    "cup": ("volume", 0.2365882365), "floz": ("volume", 0.0295735295625),
    # speed, base m/s
    "m/s": ("speed", 1.0), "km/h": ("speed", 1000.0 / 3600.0), "mph": ("speed", 1609.344 / 3600.0),
    "knot": ("speed", 1852.0 / 3600.0),
    # time, base second
    "s": ("time", 1.0), "min": ("time", 60.0), "h": ("time", 3600.0), "day": ("time", 86400.0),
    "week": ("time", 604800.0),
    # data, base byte
    "b": ("data", 1.0), "kb": ("data", 1024.0), "mb": ("data", 1024.0 ** 2), "gb": ("data", 1024.0 ** 3),
    "tb": ("data", 1024.0 ** 4),
    # temperature is handled separately (offsets, not factors)
    "c": ("temperature", 1.0), "f": ("temperature", 1.0), "k": ("temperature", 1.0),
}

_ALIASES: Dict[str, str] = {
    "millimeter": "mm", "millimetre": "mm", "centimeter": "cm", "centimetre": "cm",
    "meter": "m", "metre": "m", "kilometer": "km", "kilometre": "km", "kms": "km",
    "inch": "in", "inches": "in", "foot": "ft", "feet": "ft", "yard": "yd", "mile": "mi",
    "milligram": "mg", "gram": "g", "gm": "g", "kilogram": "kg", "kilo": "kg", "tonne": "t", "ton": "t",
    "ounce": "oz", "pound": "lb", "lbs": "lb",
    "milliliter": "ml", "millilitre": "ml", "liter": "l", "litre": "l", "ltr": "l", "gallon": "gal",
    "fluid ounce": "floz", "fl oz": "floz",
    "kph": "km/h", "kmph": "km/h", "kmh": "km/h", "knots": "knot",
    "sec": "s", "second": "s", "minute": "min", "mins": "min", "hour": "h", "hr": "h", "hrs": "h",
    "days": "day", "weeks": "week",
    "byte": "b", "kilobyte": "kb", "megabyte": "mb", "gigabyte": "gb", "terabyte": "tb",
    "celsius": "c", "centigrade": "c", "°c": "c", "fahrenheit": "f", "°f": "f", "kelvin": "k",
}


def _canonical(unit: str) -> Optional[str]:
    u = unit.strip().lower().replace("°", "").rstrip(".")
    if u in _UNITS:
        return u
    if u in _ALIASES:
        return _ALIASES[u]
    # plural: "meters", "kilograms", "gallons", "yards"
    if u.endswith("es") and u[:-2] in _ALIASES:
        return _ALIASES[u[:-2]]
    if u.endswith("s") and u[:-1] in _ALIASES:
        return _ALIASES[u[:-1]]
    if u.endswith("s") and u[:-1] in _UNITS:
        return u[:-1]
    return None


def _temperature(value: float, fu: str, tu: str) -> float:
    celsius = {"c": value, "f": (value - 32.0) * 5.0 / 9.0, "k": value - 273.15}[fu]
    return {"c": celsius, "f": celsius * 9.0 / 5.0 + 32.0, "k": celsius + 273.15}[tu]


class UnitConverterTool(BaseTool):
    """Tool for converting units of length, mass, volume, speed, time, data and temperature."""

    name = "unit_converter"
    description = (
        "Converts physical values between units: length (km, miles, feet…), mass (kg, lbs, oz), "
        "volume (litres, gallons), speed (km/h, mph), time, data sizes and temperature."
    )
    permission_level = PermissionLevel.LOW_RISK_ACTION
    input_schema = ToolParameterSchema(
        type="object",
        properties={
            "value": {"type": "number", "description": "Numeric value to convert."},
            "from_unit": {"type": "string", "description": "Starting unit, e.g. 'km', 'miles', 'kg', 'fahrenheit'."},
            "to_unit": {"type": "string", "description": "Target unit, e.g. 'miles', 'feet', 'lbs', 'celsius'."},
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
        try:
            value = float(value)
        except (TypeError, ValueError):
            raise ToolError(f"'{value}' is not a number.", speech="I need a number to convert.")
        fu, tu = _canonical(from_unit), _canonical(to_unit)
        unknown = [u for u, c in ((from_unit, fu), (to_unit, tu)) if c is None]
        if unknown:
            raise ToolError(
                f"I don't know the unit '{unknown[0]}'. I know length, mass, volume, speed, "
                f"time, data sizes and temperature — e.g. km, miles, kg, lbs, litres, mph, celsius.",
                speech=f"I don't know the unit {unknown[0]}.",
            )
        dim_from, dim_to = _UNITS[fu][0], _UNITS[tu][0]
        if dim_from != dim_to:
            raise ToolError(
                f"{from_unit} is a {dim_from} and {to_unit} is a {dim_to} — they don't convert.",
                speech=f"{from_unit} and {to_unit} measure different things.",
            )
        if dim_from == "temperature":
            res = _temperature(value, fu, tu)
        else:
            res = value * _UNITS[fu][1] / _UNITS[tu][1]

        res_rounded = round(res, 4)
        shown = f"{value:g}"
        return {
            "value": value,
            "from_unit": fu,
            "to_unit": tu,
            "result": res_rounded,
            "formatted": f"{shown} {from_unit} = {res_rounded:g} {to_unit}",
            "speech": f"{shown} {from_unit} is {res_rounded:g} {to_unit}.",
        }
