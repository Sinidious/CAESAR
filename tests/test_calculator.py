"""Tests for the calculator worker (ADR-0028, v1.3).

Two layers:

1. Unit tests on :func:`caesar.legion.calculator.evaluate` cover the
   safe-AST evaluator: numeric literals, operators, math functions,
   constants, and the rejection of every dangerous Python construct
   (attribute access, lambdas, comprehensions, imports, etc.).
2. A handler-level test feeds the worker a synthetic ``TaskDispatch``
   and verifies the wire-shape contract documented in the module
   docstring.

Bus-backed end-to-end tests live in :mod:`tests.test_legion_*`; the
calculator inherits the gated NATS subprocess from the existing
worker test harness.
"""

from __future__ import annotations

import math

import pytest

from caesar.legion.calculator import (
    CAPABILITY,
    WORKER_ID,
    CalculatorError,
    CalculatorWorker,
    evaluate,
)
from caesar.legion.protocol import TaskDispatch

# --- evaluate(): happy paths -------------------------------------------------


@pytest.mark.parametrize(
    ("expression", "expected"),
    [
        ("1 + 1", 2),
        ("5 * (12 + 8)", 100),
        ("2 ** 10", 1024),
        ("7 // 2", 3),
        ("7 % 2", 1),
        ("-3 + 5", 2),
        ("+0.5 * 4", 2.0),
        ("100 / 4", 25.0),
    ],
)
def test_evaluate_basic_arithmetic(expression: str, expected: float) -> None:
    assert evaluate(expression) == expected


def test_evaluate_math_functions() -> None:
    assert evaluate("sqrt(16)") == 4.0
    assert evaluate("log(e)") == pytest.approx(1.0)
    assert evaluate("sin(0)") == 0.0
    assert evaluate("cos(0)") == 1.0
    assert evaluate("hypot(3, 4)") == 5.0
    assert evaluate("floor(2.7)") == 2.0
    assert evaluate("ceil(2.1)") == 3.0


def test_evaluate_known_constants() -> None:
    assert evaluate("pi") == pytest.approx(math.pi)
    assert evaluate("e") == pytest.approx(math.e)
    assert evaluate("tau") == pytest.approx(math.tau)


def test_evaluate_nested_call_round_trip() -> None:
    assert evaluate("round(sqrt(2) * 10)") == 14


# --- evaluate(): safety rejections -------------------------------------------


@pytest.mark.parametrize(
    "expression",
    [
        "__import__('os').system('echo pwned')",  # imports rejected
        "open('/etc/passwd')",                    # arbitrary calls rejected
        "(1).bit_length()",                       # method calls rejected
        "[x for x in range(10)]",                 # comprehensions rejected
        "{1: 2}",                                 # literals beyond numbers rejected
        "lambda x: x",                            # lambdas rejected
        "x = 5",                                  # assignments rejected (statement mode)
        "x",                                      # unknown name rejected
        "sqrt",                                   # bare function name rejected
        "sqrt(4, base=2)",                        # keyword arguments rejected
        "1 & 2",                                  # bitwise ops not in whitelist
        "1 < 2",                                  # comparisons not in whitelist
        "True",                                   # bool literal rejected
        "None",                                   # None rejected
        "'hello'",                                # string rejected
    ],
)
def test_evaluate_rejects_dangerous_inputs(expression: str) -> None:
    with pytest.raises(CalculatorError):
        evaluate(expression)


def test_evaluate_rejects_empty_string() -> None:
    with pytest.raises(CalculatorError, match="non-empty"):
        evaluate("")


def test_evaluate_rejects_overlong_input() -> None:
    with pytest.raises(CalculatorError, match="too long"):
        evaluate("1+" * 200)


def test_evaluate_rejects_non_string() -> None:
    with pytest.raises(CalculatorError, match="non-empty string"):
        evaluate(42)  # type: ignore[arg-type]


def test_evaluate_division_by_zero_is_a_calculator_error() -> None:
    with pytest.raises(CalculatorError, match="division by zero"):
        evaluate("1 / 0")


def test_evaluate_syntax_error_is_a_calculator_error() -> None:
    with pytest.raises(CalculatorError, match="invalid syntax"):
        evaluate("1 +")


# --- worker handler contract -------------------------------------------------


def test_worker_metadata() -> None:
    assert CalculatorWorker.worker_id == WORKER_ID == "calculator"
    assert CalculatorWorker.capabilities == [CAPABILITY] == ["tool.calculator"]


async def test_handle_returns_value_and_echoes_expression() -> None:
    worker = CalculatorWorker(bus=None)  # type: ignore[arg-type]
    task = TaskDispatch(
        task_id="t1",
        capability=CAPABILITY,
        payload={"expression": "5 * (12 + 8)"},
    )
    result = await worker.handle(task)
    assert result == {"expression": "5 * (12 + 8)", "value": 100.0}


async def test_handle_rejects_non_string_expression() -> None:
    worker = CalculatorWorker(bus=None)  # type: ignore[arg-type]
    task = TaskDispatch(
        task_id="t1",
        capability=CAPABILITY,
        payload={"expression": 42},
    )
    with pytest.raises(ValueError, match="must be a string"):
        await worker.handle(task)


async def test_handle_propagates_safety_rejection_as_value_error() -> None:
    worker = CalculatorWorker(bus=None)  # type: ignore[arg-type]
    task = TaskDispatch(
        task_id="t1",
        capability=CAPABILITY,
        payload={"expression": "open('x')"},
    )
    with pytest.raises(ValueError, match="unknown function"):
        await worker.handle(task)
