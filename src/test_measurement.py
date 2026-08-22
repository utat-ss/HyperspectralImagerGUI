r"""
Checklist for the Measurement data model + storage helpers (no cameras).

    .\.venv\Scripts\python.exe src\test_measurement.py
"""

from __future__ import annotations

import json
import shutil
import sys
import tempfile
from pathlib import Path

import numpy as np
from PIL import Image

# Allow `python src/test_measurement.py` from the repo root.
sys.path.insert(0, str(Path(__file__).resolve().parent))

from core.measurement import (
    AcquisitionInfo,
    CameraCaptureConfig,
    Measurement,
    ProcessingStep,
    ScanConfig,
)
from core.measurement_storage import MeasurementStorage

_failures = []


def check(label, actual, expected):
    passed = actual == expected
    print(f"[{'ok  ' if passed else 'FAIL'}] {label}: {actual!r}")
    if not passed:
        _failures.append(label)


def check_true(label, condition):
    print(f"[{'ok  ' if condition else 'FAIL'}] {label}: {condition!r}")
    if not condition:
        _failures.append(label)


#######################################################
#          MEASUREMENT MODEL TESTING                  #
#######################################################
if __name__ == "__main__":
    tmp = Path(tempfile.mkdtemp(prefix="meas_test_"))
    try:
        storage = MeasurementStorage(tmp)

        # --- 1) Create ---
        print("--- create ---")
        acquisition = AcquisitionInfo(
            basler=CameraCaptureConfig(
                name="Basler Emulation",
                serial="0815-0000",
                exposure_us=25000.0,
                gain=3.0,
                bit_depth=8,
                pixel_format="BayerRG8",
            ),
            thorlabs=CameraCaptureConfig(
                name="Thorlabs CS135MUN",
                exposure_us=10000.0,
                bit_depth=12,
            ),
            scan=ScanConfig(step_mm=5.0, num_positions=3, notes="bench setup"),
        )
        measurement = storage.create(
            measurement_id="meas_demo_001",
            name="demo scan",
            comments="unit test measurement",
            acquisition=acquisition,
        )
        check("id", measurement.id, "meas_demo_001")
        check("name", measurement.name, "demo scan")
        check("comments", measurement.comments, "unit test measurement")
        folder = storage.measurement_dir(measurement.id)
        check_true("folder exists", folder.is_dir())
        check_true("metadata.json exists", (folder / "metadata.json").is_file())
        check_true("raw/basler exists", (folder / "raw" / "basler").is_dir())
        check_true("raw/thorlabs exists", (folder / "raw" / "thorlabs").is_dir())
        check_true("processed exists", (folder / "processed").is_dir())

        # --- 2) Raw frames + datacube + thumbnail ---
        print("--- raw frames / datacube / thumbnail ---")
        basler_frame = np.zeros((32, 48, 3), dtype=np.uint8)
        basler_frame[:, :, 0] = 200
        rel_b = storage.add_raw_frame(measurement, "basler", basler_frame)
        check("basler relative path", rel_b, "raw/basler/frame_000.png")
        check("basler frames count", len(measurement.raw.basler_frames), 1)

        thor_frame = np.arange(64, dtype=np.uint16).reshape(8, 8)
        rel_t = storage.add_raw_frame(measurement, "thorlabs", thor_frame)
        check("thorlabs relative path", rel_t, "raw/thorlabs/frame_000.npy")
        check("thorlabs frames count", len(measurement.raw.thorlabs_frames), 1)

        cube = np.random.randint(0, 1000, size=(4, 8, 16), dtype=np.uint16)
        rel_cube = storage.set_datacube(measurement, cube)
        check("datacube path", rel_cube, "raw/datacube.npy")

        thumb = np.full((16, 16, 3), 40, dtype=np.uint8)
        rel_thumb = storage.set_thumbnail(measurement, thumb)
        check("thumbnail path", rel_thumb, "thumbnail.png")
        check_true("thumbnail file exists", (folder / "thumbnail.png").is_file())

        # --- 3) Processed product + history ---
        print("--- processed product ---")
        steps = [
            ProcessingStep(operation="dark_frame_correction", parameters={}),
            ProcessingStep(
                operation="compute_ndvi",
                parameters={"nir_band": 10, "red_band": 5},
            ),
        ]
        product_array = (cube.astype(np.float32) / 1000.0).mean(axis=2)
        product = storage.add_processed_product(
            measurement,
            product_id="ndvi_001",
            kind="ndvi",
            array=product_array,
            steps=steps,
            annotation="test NDVI",
            preview=product_array,
            preview_vmin=-1.0,
            preview_vmax=1.0,
        )
        check("product id", product.product_id, "ndvi_001")
        check("product kind", product.kind, "ndvi")
        check("product steps", len(product.steps), 2)
        check(
            "product data path",
            product.data_path,
            "processed/ndvi_001/product.npy",
        )
        check_true(
            "steps.log exists",
            (folder / "processed" / "ndvi_001" / "steps.log").is_file(),
        )
        check(
            "find_product",
            measurement.find_product("ndvi_001") is product,
            True,
        )

        # --- 4) Save / load round-trip ---
        print("--- save / load round-trip ---")
        storage.save(measurement)
        loaded = storage.load("meas_demo_001")
        check("loaded id", loaded.id, measurement.id)
        check("loaded name", loaded.name, measurement.name)
        check("loaded comments", loaded.comments, measurement.comments)
        check(
            "loaded basler exposure",
            loaded.acquisition.basler.exposure_us,
            25000.0,
        )
        check(
            "loaded thorlabs bit_depth",
            loaded.acquisition.thorlabs.bit_depth,
            12,
        )
        check("loaded scan step_mm", loaded.acquisition.scan.step_mm, 5.0)
        check("loaded basler frames", loaded.raw.basler_frames, ["raw/basler/frame_000.png"])
        check("loaded thorlabs frames", loaded.raw.thorlabs_frames, ["raw/thorlabs/frame_000.npy"])
        check("loaded datacube", loaded.raw.datacube, "raw/datacube.npy")
        check("loaded thumbnail", loaded.thumbnail, "thumbnail.png")
        check("loaded processed count", len(loaded.processed), 1)
        loaded_product = loaded.processed[0]
        check("loaded product kind", loaded_product.kind, "ndvi")
        check("loaded product annotation", loaded_product.annotation, "test NDVI")
        check("loaded step ops",
              [s.operation for s in loaded_product.steps],
              ["dark_frame_correction", "compute_ndvi"])
        check(
            "loaded step params",
            loaded_product.steps[1].parameters,
            {"nir_band": 10, "red_band": 5},
        )

        # Paths stay relative (portable) and arrays reload.
        meta = json.loads((folder / "metadata.json").read_text(encoding="utf-8"))
        check_true(
            "json basler path is relative",
            not Path(meta["raw"]["basler_frames"][0]).is_absolute(),
        )
        reloaded_cube = storage.load_array(loaded, loaded.raw.datacube)
        check_true("datacube round-trip equal", np.array_equal(reloaded_cube, cube))
        reloaded_product = storage.load_array(loaded, loaded_product.data_path)
        check_true(
            "product array round-trip equal",
            np.allclose(reloaded_product, product_array),
        )

        # --- 5) Optional fields / guards ---
        print("--- optional fields / guards ---")
        bare = storage.create(measurement_id="meas_bare")
        check("bare name default", bare.name, "")
        check("bare comments default", bare.comments, "")
        check("bare basler None", bare.acquisition.basler, None)
        check("bare datacube None", bare.raw.datacube, None)
        check("bare processed empty", bare.processed, [])
        check("bare thumbnail None", bare.thumbnail, None)

        nan_preview = np.array([[-1.0, 0.0], [1.0, np.nan]], dtype=np.float32)
        storage.add_processed_product(
            bare,
            product_id="preview_scale",
            kind="ndvi",
            array=nan_preview,
            preview=nan_preview,
            preview_vmin=-1.0,
            preview_vmax=1.0,
        )
        preview_png = np.array(
            Image.open(
                storage.measurement_dir(bare.id)
                / "processed"
                / "preview_scale"
                / "preview.png"
            )
        )
        check("fixed-range -1 -> 0", int(preview_png[0, 0, 0]), 0)
        check("fixed-range 0 -> 128", int(preview_png[0, 1, 0]), 128)
        check("fixed-range 1 -> 255", int(preview_png[1, 0, 0]), 255)
        check("nan alpha is 0", int(preview_png[1, 1, -1]), 0)

        try:
            storage.create(measurement_id="meas_demo_001")
            check_true("duplicate create raises", False)
        except FileExistsError:
            check_true("duplicate create raises", True)

        try:
            storage.add_processed_product(
                measurement,
                product_id="ndvi_001",
                kind="ndvi",
                array=product_array,
            )
            check_true("duplicate product raises", False)
        except ValueError:
            check_true("duplicate product raises", True)

        print()
        if _failures:
            sys.exit(f"ERR: {len(_failures)} check(s) failed: {', '.join(_failures)}")
        print("Measurement model testing successful!")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
