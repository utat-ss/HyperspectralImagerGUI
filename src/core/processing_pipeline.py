
from core.processing_operation import ProcessingData, ProcessingOperation


class ProcessingPipeline(ProcessingOperation):
    """Executes a sequence of processing operations in order."""

    def __init__(self, operations: list[ProcessingOperation] | None = None):
        self._operations = list(operations) if operations is not None else []

    def add_operation(self, operation: ProcessingOperation) -> None:
        """Append an operation to the pipeline."""
        self._operations.append(operation)

    def remove_operation(self, index: int) -> None:
        """Remove an operation at the specified index."""
        if not 0 <= index < len(self._operations):
            raise IndexError("Operation index out of range")

        self._operations.pop(index)

    def move_operation(self, old_index: int, new_index: int) -> None:
        """Move an operation to a different position."""
        if not 0 <= old_index < len(self._operations):
            raise IndexError("Source index out of range")

        if not 0 <= new_index < len(self._operations):
            raise IndexError("Destination index out of range")

        operation = self._operations.pop(old_index)
        self._operations.insert(new_index, operation)

    def process(self, data: ProcessingData) -> ProcessingData:
        """Apply each operation sequentially."""
        for operation in self._operations:
            data = operation.process(data)
        return data

    def __str__(self) -> str:
        """Return a numbered list of processing operations."""
        return "\n".join(
            f"{index}: {type(operation).__name__}"
            for index, operation in enumerate(self._operations)
        )
