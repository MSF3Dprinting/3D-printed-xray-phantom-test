import os
import sys

import numpy as np
import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

SAMPLE_DIR = os.path.join(
    ROOT, "20260727 MSF", "MSF", "Export_2026-07-27_10-00-05_1",
    "10000000", "10000001")
SAMPLES = [os.path.join(SAMPLE_DIR, "10000002", "10000003"),
           os.path.join(SAMPLE_DIR, "10000004", "10000005")]

needs_samples = pytest.mark.skipif(
    not all(os.path.exists(p) for p in SAMPLES),
    reason="sample DICOM scans not present")


@pytest.fixture(scope="session")
def rng():
    return np.random.default_rng(42)


@pytest.fixture(scope="session")
def sample_scans():
    from phantom_qa import ingest
    return [ingest.load_path(p)[0] for p in SAMPLES]


@pytest.fixture(scope="session")
def pdef():
    from phantom_qa.phantom_def import load_default
    return load_default()
