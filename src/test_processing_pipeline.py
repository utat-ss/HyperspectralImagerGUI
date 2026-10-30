
"""
Checklist for ProcessingPipeline.

    .\.venv\Scripts\python.exe src\test_processing_pipeline.py
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))

from core.processing_operation import ProcessingData, ProcessingOperation
from core.processing_pipeline import ProcessingPipeline


_failures = []


def check_true(label, condition):
    print(f"[{'ok  ' if condition else 'FAIL'}] {label}: {condition!r}")
    if not condition:
        _failures.append(label)


class AddOneOperation(ProcessingOperation):
    def process(self, data: ProcessingData) -> ProcessingData:
        return ProcessingData(data.array + 1)


class DoubleOperation(ProcessingOperation):
    def process(self, data: ProcessingData) -> ProcessingData:
        return ProcessingData(data.array * 2)


if __name__ == "__main__":
    input_data = ProcessingData(np.array([1, 2, 3]))

    print("--- sequential execution and data flow ---")

    pipeline = ProcessingPipeline([
        AddOneOperation(),
        DoubleOperation(),
    ])

    result = pipeline.process(input_data)

    check_true(
        "operations run in order",
        np.array_equal(result.array, np.array([4, 6, 8])),
    )

    check_true(
        "result is ProcessingData",
        isinstance(result, ProcessingData),
    )

    print("--- empty pipeline ---")

    empty_pipeline = ProcessingPipeline()
    result = empty_pipeline.process(input_data)

    check_true(
        "empty pipeline returns original data",
        result is input_data,
    )

    print("--- dynamic configuration ---")

    pipeline = ProcessingPipeline()
    pipeline.add_operation(DoubleOperation())
    pipeline.add_operation(AddOneOperation())

    result = pipeline.process(input_data)

    check_true(
        "added operations execute in order",
        np.array_equal(result.array, np.array([3, 5, 7])),
    )

    print("--- nested pipelines ---")

    inner_pipeline = ProcessingPipeline([
        AddOneOperation(),
        DoubleOperation(),
    ])

    outer_pipeline = ProcessingPipeline([
        inner_pipeline,
        AddOneOperation(),
    ])

    result = outer_pipeline.process(input_data)

    check_true(
        "nested pipeline works",
        np.array_equal(result.array, np.array([5, 7, 9])),
    )

    print()


    print("--- removing operations ---")

    pipeline = ProcessingPipeline([
        AddOneOperation(),
        DoubleOperation(),
    ])

    pipeline.remove_operation(0)
    result = pipeline.process(input_data)

    check_true(
        "removing an operation",
        np.array_equal(result.array, np.array([2, 4, 6])),
    )

    print("--- reordering operations ---")

    pipeline = ProcessingPipeline([
        AddOneOperation(),
        DoubleOperation(),
    ])

    pipeline.move_operation(0, 1)
    result = pipeline.process(input_data)

    check_true(
        "reordering operations",
        np.array_equal(result.array, np.array([3, 5, 7])),
    )

    if _failures:
        sys.exit(
            f"ERR: {len(_failures)} check(s) failed: {', '.join(_failures)}"
        )

    print("ProcessingPipeline testing successful!")
