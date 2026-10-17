r"""
Checklist for ProcessingOperation.

    .\.venv\Scripts\python.exe src\test_processing_operation.py
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np

# Allow `python src/test_processing_operation.py` from the repo root.
sys.path.insert(0, str(Path(__file__).resolve().parent))

from core.processing_operation import ProcessingOperation

_failures = []


def check_true(label, condition):
    print(f"[{'ok  ' if condition else 'FAIL'}] {label}: {condition!r}")
    if not condition:
        _failures.append(label)


class DoubleOperation(ProcessingOperation):
    """Simple implementation used to verify the interface."""

    def process(self, data: np.ndarray) -> np.ndarray:
        return data * 2


class InvalidOperation(ProcessingOperation):
    """Intentionally does not implement process()."""

    pass


if __name__ == "__main__":
    print("--- concrete processing operation ---")

    operation = DoubleOperation()
    input_data = np.array([1, 2, 3])
    result = operation.process(input_data)

    check_true(
        "process returns expected result",
        np.array_equal(result, np.array([2, 4, 6])),
    )

    print("--- abstract interface ---")

    try:
        InvalidOperation()
        check_true("missing process implementation raises", False)
    except TypeError:
        check_true("missing process implementation raises", True)

    print()

    if _failures:
        sys.exit(
            f"ERR: {len(_failures)} check(s) failed: {', '.join(_failures)}"
        )

    print("ProcessingOperation testing successful!")
