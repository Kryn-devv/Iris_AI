"""Calculator tool for safe mathematical calculations using Python AST."""

import ast
import operator
from typing import Any, Dict
from nova.app.tools.base import BaseTool
from nova.app.core.security import PermissionLevel
from nova.app.schemas.tools import ToolParameterSchema

# Supported operators mapping for AST evaluation
OPERATORS = {
    ast.Add: operator.add,
    ast.Sub: operator.sub,
    ast.Mult: operator.mul,
    ast.Div: operator.truediv,
    ast.FloorDiv: operator.floordiv,
    ast.Mod: operator.mod,
    ast.Pow: operator.pow,
    ast.USub: operator.neg,
    ast.UAdd: operator.pos,
}


def safe_eval_ast(node: ast.AST) -> Any:
    """Evaluate an AST expression node safely without allowing arbitrary code execution."""
    if isinstance(node, ast.Expression):
        return safe_eval_ast(node.body)
    elif isinstance(node, ast.Constant):  # Python 3.8+ for numbers
        if isinstance(node.value, (int, float)) and not isinstance(node.value, bool):
            return node.value
        raise ValueError(f"Unsupported constant type: {type(node.value).__name__}")
    elif isinstance(node, ast.BinOp):
        left = safe_eval_ast(node.left)
        right = safe_eval_ast(node.right)
        op_type = type(node.op)
        if op_type in OPERATORS:
            if op_type in (ast.Div, ast.FloorDiv, ast.Mod) and right == 0:
                raise ZeroDivisionError("Division by zero is not allowed.")
            return OPERATORS[op_type](left, right)
        raise ValueError(f"Unsupported binary operator: {op_type.__name__}")
    elif isinstance(node, ast.UnaryOp):
        operand = safe_eval_ast(node.operand)
        op_type = type(node.op)
        if op_type in OPERATORS:
            return OPERATORS[op_type](operand)
        raise ValueError(f"Unsupported unary operator: {op_type.__name__}")
    else:
        raise ValueError(f"Unsupported expression construct: {type(node).__name__}")


class CalculatorTool(BaseTool):
    """Tool for evaluating mathematical expressions safely."""

    name = "calculator"
    description = "Evaluates basic mathematical expressions (addition, subtraction, multiplication, division, powers)."
    permission_level = PermissionLevel.LOW_RISK_ACTION
    input_schema = ToolParameterSchema(
        type="object",
        properties={
            "expression": {
                "type": "string",
                "description": "Mathematical expression to evaluate, e.g., '78 * 23 * 7' or '(100 + 50) / 3'",
            }
        },
        required=["expression"],
    )

    async def _run(self, expression: str = "", **kwargs: Any) -> Dict[str, Any]:
        if not expression or not expression.strip():
            raise ValueError("Expression parameter cannot be empty.")

        # Clean string expression
        clean_expr = expression.strip().replace("x", "*").replace("X", "*")

        try:
            tree = ast.parse(clean_expr, mode="eval")
            calc_result = safe_eval_ast(tree)
            if isinstance(calc_result, float) and calc_result.is_integer():
                calc_result = int(calc_result)

            return {
                "expression": expression,
                "result": calc_result,
                "formatted": f"{expression} = {calc_result}",
            }
        except SyntaxError as e:
            raise ValueError(f"Invalid mathematical syntax: {expression}") from e
        except ZeroDivisionError as e:
            raise ValueError("Division by zero") from e
        except Exception as e:
            raise ValueError(f"Calculation failed: {e}") from e
