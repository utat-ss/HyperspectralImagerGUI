from abc import ABC, abstractmethod

import numpy as np


class ProcessingOperation(ABC):
    """
    Base interface for an executable processing operation.

    Processing operations take captured or previously processed numerical
    data and return a NumPy array so operations can be chained together.

    The input and output may represent images, hyperspectral datacubes,
    or other array-based processing products.
    """

    @abstractmethod
    def process(self, data: np.ndarray) -> np.ndarray:
        """Process the input data and return the resulting array."""
        raise NotImplementedError
