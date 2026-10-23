from abc import ABC, abstractmethod
from dataclasses import dataclass

import numpy as np


@dataclass
class ProcessingData:
    """
    Container for data passed between processing operations.

    Currently stores a NumPy array, but can be extended with
    additional metadata as needed.
    """

    array: np.ndarray


class ProcessingOperation(ABC):
    """
    Base interface for an executable processing operation.

    Processing operations take and return ProcessingData objects,
    allowing operations to be chained together.
    """

    @abstractmethod
    def process(self, data: ProcessingData) -> ProcessingData:
        """Process the input data and return the resulting data."""
        raise NotImplementedError
