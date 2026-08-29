"""
Frame -> 1D intensity trace extraction.

PLACEHOLDER: this collapses the whole frame (mean across rows) rather
than reading a real dispersion axis -- there is no slit-orientation or
wavelength-calibration data anywhere in this repo yet (confirm with
your lead before treating this as anything but a sanity-check trace).
This is deliberately the only file that assumption lives in, so wiring
in a real spectral axis and pixel-to-wavelength calibration later is a
one-file change, not a hunt through the GUI code.

A real spectrometer would likely want a sum across the slit axis
instead of a mean (better SNR), but sum's output range depends on frame
height, whereas mean stays in the same 0..(2**bit_depth - 1) range as
the source pixels -- which is what lets the spectrum plot reuse the
same bit-depth-derived y-axis as the image view. Switching to sum later
means the y-axis needs its own scale, not the shared one.
"""

import numpy as np


def extract_spectrum(frame: np.ndarray) -> np.ndarray:
    return frame.mean(axis=0)
