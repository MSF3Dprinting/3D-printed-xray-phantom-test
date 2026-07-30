"""Ingest, metadata extraction and protocol-signature tests."""

import numpy as np
import pytest

from conftest import needs_samples

from phantom_qa.ingest import protocol_signature


@needs_samples
def test_dicom_metadata_complete(sample_scans):
    for scan in sample_scans:
        m = scan.meta
        for tag in ("ManufacturerModelName", "KVP", "ImagerPixelSpacing",
                    "PixelSpacing", "AcquisitionDeviceProcessingDescription",
                    "TransferSyntax"):
            assert tag in m, f"missing metadata tag {tag}"
        assert scan.kind == "dicom"
        assert not scan.reduced_precision
        assert scan.spacing_candidates["ImagerPixelSpacing"] == pytest.approx(0.148)
        assert len(scan.sha256) == 64


@needs_samples
def test_pixels_are_lossless_12bit(sample_scans):
    for scan in sample_scans:
        assert scan.pixels.dtype == np.float64
        assert scan.pixels.max() <= 4095
        assert scan.pixels.max() > 3000          # real dynamic range present


@needs_samples
def test_protocol_signature_discriminates(sample_scans):
    """Signature must carry model, kV and pixel spacing — the fields that decide
    whether two analyses are comparable."""
    sigs = [protocol_signature(s.meta) for s in sample_scans]
    for sig in sigs:
        assert "DigitalDiagnost C50" in sig
        assert "77 kV" in sig
        assert "0.148 mm/px" in sig
        assert "?" not in sig
    # the two reference scans share model/kV/spacing/processing family
    assert sigs[0] == sigs[1]


def test_signature_missing_metadata_is_explicit():
    sig = protocol_signature({})
    assert "unknown" in sig and "no-processing-info" in sig


def test_signature_separates_detectors():
    a = protocol_signature({"ManufacturerModelName": "DetA", "KVP": 70,
                            "ImagerPixelSpacing": ["0.148", "0.148"]})
    b = protocol_signature({"ManufacturerModelName": "DetB", "KVP": 70,
                            "ImagerPixelSpacing": ["0.100", "0.100"]})
    assert a != b
