from abc import ABC, abstractmethod

import numpy as np


class ProcessingOperation(ABC):
    """
    Base interface for an executable processing operation.

    Processing operations take captured or previously processed numerical
    data and return the processed result.
    """

    @abstractmethod
    def process(self, data: np.ndarray) -> np.ndarray:
        """Process the input data and return the resulting array."""
        raise NotImplementedError
