
from core.processing_operation import ProcessingData, ProcessingOperation


class ProcessingPipeline(ProcessingOperation):
    """Executes a sequence of processing operations in order."""

    def __init__(self, operations: list[ProcessingOperation] | None = None):
        self.operations = list(operations) if operations is not None else []

    def add_operation(self, operation: ProcessingOperation) -> None:
        """Append an operation to the pipeline."""
        self.operations.append(operation)

    def remove_operation(self, index: int) -> None:
        """Remove an operation at the specified index."""
        self.operations.pop(index)

    def move_operation(self, old_index: int, new_index: int) -> None:
        """Move an operation to a different position."""
        operation = self.operations.pop(old_index)
        self.operations.insert(new_index, operation)

    def process(self, data: ProcessingData) -> ProcessingData:
        """Apply each operation sequentially."""
        for operation in self.operations:
            data = operation.process(data)
        return data
