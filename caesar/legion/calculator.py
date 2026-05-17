"""Calculator worker (ADR-0028).

Pure-Python arithmetic evaluator. No network, no credentials, no
side effects. Smallest possible exercise of the v1.3 tool-worker
path.

Input payload (dispatched from the brain graph via
``caesar.dispatch.tool.calculator``):

.. code-block:: json

    { "expression": "5 * (12 + 8)" }

Output:

.. code-block:: json

    { "value": 100, "expression": "5 * (12 + 8)" }

The evaluator uses :mod:`ast` rather than :func:`eval` so the input
space is bounded to a known-safe subset: numeric literals, parens,
unary ``+/-``, binary ``+ - * / // % **``, and a small whitelist of
:mod:`math` functions (sqrt, log, log2, log10, exp, sin, cos, tan,
asin, acos, atan, atan2, hypot, floor, ceil, abs, round, pow).
Anything else raises :class:`CalculatorError`.
"""

from __future__ import annotations

import ast
import math
from collections.abc import Callable
from typing import ClassVar

from caesar.bus.client import Bus
from caesar.legion.protocol import TaskDispatch
from caesar.legion.worker import Worker

CAPABILITY = "tool.calculator"
WORKER_ID = "calculator"

_BINOPS: dict[type[ast.AST], Callable[[float, float], float]] = {
    ast.Add: lambda a, b: a + b,
    ast.Sub: lambda a, b: a - b,
    ast.Mult: lambda a, b: a * b,
    ast.Div: lambda a, b: a / b,
    ast.FloorDiv: lambda a, b: a // b,
    ast.Mod: lambda a, b: a % b,
    ast.Pow: lambda a, b: a**b,
}

_UNARYOPS: dict[type[ast.AST], Callable[[float], float]] = {
    ast.UAdd: lambda a: +a,
    ast.USub: lambda a: -a,
}

_FUNCS: dict[str, Callable[..., float]] = {
    "sqrt": math.sqrt,
    "log": math.log,
    "log2": math.log2,
    "log10": math.log10,
    "exp": math.exp,
    "sin": math.sin,
    "cos": math.cos,
    "tan": math.tan,
    "asin": math.asin,
    "acos": math.acos,
    "atan": math.atan,
    "atan2": math.atan2,
    "hypot": math.hypot,
    "floor": math.floor,
    "ceil": math.ceil,
    "abs": abs,
    "round": round,
    "pow": pow,
}

_CONSTANTS: dict[str, float] = {
    "pi": math.pi,
    "e": math.e,
    "tau": math.tau,
}


class CalculatorError(ValueError):
    """The expression was syntactically valid Python but not safe arithmetic."""


def _eval_node(node: ast.AST) -> float:
    if isinstance(node, ast.Expression):
        return _eval_node(node.body)
    if isinstance(node, ast.Constant):
        # ``bool`` is an ``int`` subclass; reject it explicitly so
        # ``True / False`` aren't silently treated as 1 / 0.
        if isinstance(node.value, bool):
            raise CalculatorError(f"unsupported constant: {node.value!r}")
        if isinstance(node.value, int | float):
            return float(node.value)
        raise CalculatorError(f"unsupported constant: {node.value!r}")
    if isinstance(node, ast.Name):
        if node.id in _CONSTANTS:
            return _CONSTANTS[node.id]
        raise CalculatorError(f"unknown name: {node.id!r}")
    if isinstance(node, ast.UnaryOp):
        unary = _UNARYOPS.get(type(node.op))
        if unary is None:
            raise CalculatorError(f"unsupported unary operator: {type(node.op).__name__}")
        return unary(_eval_node(node.operand))
    if isinstance(node, ast.BinOp):
        binary = _BINOPS.get(type(node.op))
        if binary is None:
            raise CalculatorError(f"unsupported operator: {type(node.op).__name__}")
        return binary(_eval_node(node.left), _eval_node(node.right))
    if isinstance(node, ast.Call):
        if not isinstance(node.func, ast.Name):
            raise CalculatorError("function calls must use a bare name")
        if node.keywords:
            raise CalculatorError("keyword arguments are not supported")
        fn = _FUNCS.get(node.func.id)
        if fn is None:
            raise CalculatorError(f"unknown function: {node.func.id!r}")
        return float(fn(*[_eval_node(arg) for arg in node.args]))
    raise CalculatorError(f"unsupported expression element: {type(node).__name__}")


def evaluate(expression: str) -> float:
    """Evaluate ``expression`` safely; raise :class:`CalculatorError` on misuse."""

    if not isinstance(expression, str) or not expression.strip():
        raise CalculatorError("expression must be a non-empty string")
    if len(expression) > 256:
        raise CalculatorError("expression too long (max 256 chars)")
    try:
        tree = ast.parse(expression, mode="eval")
    except SyntaxError as exc:
        raise CalculatorError(f"invalid syntax: {exc.msg}") from exc
    try:
        result = _eval_node(tree)
    except ZeroDivisionError as exc:
        raise CalculatorError("division by zero") from exc
    except OverflowError as exc:
        raise CalculatorError("result overflowed") from exc
    return result


class CalculatorWorker(Worker):
    """Arithmetic-evaluator Legion worker."""

    worker_id: ClassVar[str] = WORKER_ID
    capabilities: ClassVar[list[str]] = [CAPABILITY]
    version: ClassVar[str] = "0.3.0"

    def __init__(self, bus: Bus) -> None:
        super().__init__(bus)

    async def handle(self, task: TaskDispatch) -> dict[str, object]:
        raw_expr = task.payload.get("expression")
        if not isinstance(raw_expr, str):
            raise ValueError("'expression' must be a string")
        try:
            value = evaluate(raw_expr)
        except CalculatorError as exc:
            raise ValueError(str(exc)) from exc
        return {"expression": raw_expr, "value": value}
