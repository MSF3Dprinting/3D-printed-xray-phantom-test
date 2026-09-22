"""Loading and normalization of phantom scans (DICOM preferred, plain images fallback).

Analysis convention after normalization: **higher pixel value = more attenuation**
(the phantom appears bright, direct exposure dark). All statistics downstream are
computed on ``ScanData.pixels`` in float64.
"""

from __future__ import annotations

import hashlib
import io
import logging
import math
import os
import zipfile
from dataclasses import dataclass, field

import numpy as np

log = logging.getLogger("phantomqa.ingest")

DICOM_EXTENSIONS = {".dcm", ".dicom", ""}
IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".tif", ".tiff", ".bmp"}


@dataclass
class ScanData:
    pixels: np.ndarray            # float64, higher = more attenuation
    meta: dict                    # extracted metadata (JSON-serializable)
    sha256: str
    source_name: str
    kind: str                     # 'dicom' | 'image'
    reduced_precision: bool       # True for plain-image fallback
    spacing_candidates: dict = field(default_factory=dict)  # name -> mm/px
    #: The exact bytes this scan was decoded from. For a zip (CD export) that
    #: is the extracted MEMBER, which is what sha256 fingerprints — the caller
    #: that persists an upload must store these, not the container, or every
    #: later integrity check compares the container's hash against the
    #: member's and fails. Never serialised; in-memory hand-off only.
    source_bytes: bytes | None = None

    @property
    def shape(self):
        return self.pixels.shape


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


#: The detector's own account of how much radiation reached it (IEC 62494-1):
#: the exposure index, the index it was set up to expect, the deviation of one
#: from the other, and a sensitivity (the Fuji's S value). All three detectors
#: in use write the exposure index; the Carestream and the field Fuji also
#: write the other three, the Philips does not.
#:
#: They are what tells an operator at a glance that a scan was under- or
#: over-exposed, which is the first thing to rule out when its numbers look
#: wrong. Stored as NUMBERS, unlike the rest of the header, so they can be
#: listed and exported without re-parsing text.
EXPOSURE_TAGS = ("ExposureIndex", "TargetExposureIndex", "DeviationIndex",
                 "Sensitivity")


def _dicom_number(value):
    """A header value as an int or float, or None when there is none.

    Read from the text the detector wrote rather than from pydicom's float, so
    an index written as "251" is stored as 251 and not as 251.0. A blank, a
    malformed value or a non-finite one is treated as absent: an exposure index
    that is not a real number is not one, and inventing a value is worse than
    showing that the detector recorded none."""
    if isinstance(value, (list, tuple)) or \
            value.__class__.__name__ == "MultiValue":
        value = value[0] if len(value) else None
    if value is None or isinstance(value, bool):
        return None
    text = str(value).strip()
    if not text:
        return None
    try:
        return int(text)
    except ValueError:
        pass
    try:
        number = float(text)
    except ValueError:
        return None
    return number if math.isfinite(number) else None


def exposure_values(ds) -> dict:
    """The exposure tags this dataset carries, as numbers. Absent stays absent."""
    out = {}
    for tag in EXPOSURE_TAGS:
        number = _dicom_number(getattr(ds, tag, None))
        if number is not None:
            out[tag] = number
    return out


def read_exposure_header(path: str) -> dict:
    """The exposure tags of a stored DICOM, reading its header only.

    Used to fill in records stored before these tags were kept. Stopping before
    the pixel data is what makes that affordable at start-up: a detector image
    is tens of megabytes, its header a few kilobytes."""
    import pydicom

    ds = pydicom.dcmread(path, stop_before_pixels=True)
    return exposure_values(ds)


def _extract_dicom_meta(ds) -> dict:
    tags = [
        "SOPInstanceUID", "SOPClassUID", "Modality", "Manufacturer",
        "ManufacturerModelName", "StationName", "DetectorID", "DetectorType",
        "StudyDate", "StudyTime", "SeriesTime", "AcquisitionDate",
        "AcquisitionTime",
        "StudyDescription", "SeriesDescription", "ProtocolName",
        "BodyPartExamined", "ViewPosition", "PatientOrientation",
        "KVP", "ExposureTime", "XRayTubeCurrent", "Exposure", "ExposureInuAs",
        "RelativeXRayExposure", "SensitivityValue",
        "DistanceSourceToDetector", "DistanceSourceToPatient",
        "EstimatedRadiographicMagnificationFactor",
        "ImagerPixelSpacing", "PixelSpacing", "SpatialResolution",
        "Rows", "Columns", "BitsAllocated", "BitsStored", "HighBit",
        "PixelRepresentation", "PhotometricInterpretation",
        "RescaleSlope", "RescaleIntercept", "RescaleType",
        "WindowCenter", "WindowWidth", "PresentationLUTShape",
        "AcquisitionDeviceProcessingDescription", "ImageType",
    ]
    meta = {}
    for t in tags:
        if hasattr(ds, t):
            v = getattr(ds, t)
            try:
                if isinstance(v, (list, tuple)) or v.__class__.__name__ == "MultiValue":
                    meta[t] = [str(x) for x in v]
                elif isinstance(v, (int, float)):
                    meta[t] = v
                else:
                    meta[t] = str(v)
            except Exception:
                meta[t] = str(v)
    meta.update(exposure_values(ds))
    return meta


def protocol_signature(meta: dict) -> str:
    """Compact identifier of acquisition conditions used to group comparable scans."""
    model = meta.get("ManufacturerModelName", "unknown")
    kvp = meta.get("KVP", "?")
    spacing = meta.get("ImagerPixelSpacing") or meta.get("PixelSpacing") or ["?"]
    proc = str(meta.get("AcquisitionDeviceProcessingDescription", ""))
    # Keep the processing family (e.g. "UNIQUE: S:200 L:4.0"), drop per-exposure params.
    proc_family = " ".join(proc.split()[:3]) if proc else "no-processing-info"
    return f"{model} | {kvp} kV | {spacing[0]} mm/px | {proc_family}"


def _invert_monochrome1(values, stored, ds, slope: float, intercept: float):
    """Turn a MONOCHROME1 image (bigger number = darker) the other way up.

    Flipped about the detector's own range, taken from the bit depth, so the
    same exposure of the same object always comes out with the same values.

    It used to be flipped about the brightest pixel in the image. That is the
    same thing only when some pixel happens to reach the detector's maximum —
    true on every readable field scan so far, because the unblocked beam at the
    image edge saturates the 10-bit Fuji at 1023. On an exposure where nothing
    reaches the maximum (collimated inside the phantom, or a lower dose) every
    value shifted by an amount that depended on the picture, which moved the
    uniformity signal-to-noise and the wedge ratio from scan to scan for no
    physical reason.

    Falls back to the old rule — and says so in the returned note — only when
    the header gives no usable range, because a wrong declared range would be
    worse than a content-dependent one.
    """
    bits = getattr(ds, "BitsStored", None) or getattr(ds, "BitsAllocated", None)
    signed = int(getattr(ds, "PixelRepresentation", 0) or 0) == 1
    reason = "the header declares no bit depth"
    if bits:
        bits = int(bits)
        lo, hi = ((-(1 << (bits - 1)), (1 << (bits - 1)) - 1) if signed
                  else (0, (1 << bits) - 1))
        smin, smax = float(stored.min()), float(stored.max())
        if lo <= smin and smax <= hi:
            # The flip of the stored value, carried through the rescale:
            # ((lo + hi) - stored) * slope + intercept.
            pivot = (lo + hi) * slope + 2.0 * intercept
            return pivot - values, {"method": "bit depth", "bits": bits,
                                    "range": [lo, hi]}
        reason = (f"pixel values {smin:.0f}..{smax:.0f} fall outside the "
                  f"declared {bits}-bit range")
    log.warning("MONOCHROME1 image inverted about its own maximum: %s", reason)
    return float(values.max()) - values, {"method": "image maximum",
                                          "reason": reason}


def load_dicom_bytes(data: bytes, source_name: str) -> ScanData:
    import pydicom

    ds = pydicom.dcmread(io.BytesIO(data))
    stored = ds.pixel_array.astype(np.float64)

    slope = float(getattr(ds, "RescaleSlope", 1) or 1)
    intercept = float(getattr(ds, "RescaleIntercept", 0) or 0)
    arr = stored * slope + intercept

    photometric = str(getattr(ds, "PhotometricInterpretation", "MONOCHROME2"))
    inversion = None
    if photometric == "MONOCHROME1":
        arr, inversion = _invert_monochrome1(arr, stored, ds, slope, intercept)

    meta = _extract_dicom_meta(ds)
    meta["TransferSyntax"] = ds.file_meta.TransferSyntaxUID.name
    meta["_polarity"] = "attenuation-high"
    if inversion:
        # Kept with the record, so a value that looks odd later can be traced
        # to how the image was turned the right way up.
        meta["_inversion"] = inversion

    spacing = {}
    if hasattr(ds, "ImagerPixelSpacing"):
        spacing["ImagerPixelSpacing"] = float(ds.ImagerPixelSpacing[0])
    if hasattr(ds, "PixelSpacing"):
        spacing["PixelSpacing"] = float(ds.PixelSpacing[0])

    return ScanData(
        pixels=arr,
        meta=meta,
        sha256=_sha256(data),
        source_name=source_name,
        kind="dicom",
        reduced_precision=False,
        spacing_candidates=spacing,
        source_bytes=data,
    )


def load_image_bytes(data: bytes, source_name: str) -> ScanData:
    from PIL import Image

    img = Image.open(io.BytesIO(data))
    arr = np.asarray(img.convert("L") if img.mode not in ("I;16", "I") else img,
                     dtype=np.float64)
    meta = {
        "Rows": arr.shape[0], "Columns": arr.shape[1],
        "SourceFormat": img.format, "_polarity": "assumed-attenuation-high",
    }
    return ScanData(
        pixels=arr,
        meta=meta,
        sha256=_sha256(data),
        source_name=source_name,
        kind="image",
        reduced_precision=True,
        spacing_candidates={},
        source_bytes=data,
    )


def _is_dicom_bytes(data: bytes) -> bool:
    return len(data) > 132 and data[128:132] == b"DICM"


def load_any_bytes(data: bytes, source_name: str) -> list[ScanData]:
    """Load a file of any supported kind. Zip archives (CD exports) are walked for
    DICOM images; DICOMDIR indexes are skipped in favor of scanning actual files."""
    name_lower = source_name.lower()
    if name_lower.endswith(".zip"):
        # Decompression bombs: infolist() reports the DECLARED uncompressed
        # size, so both caps are enforced before a byte is inflated. A real
        # detector image is tens of MB; the caps are far above any legitimate
        # CD export and far below what would take a gunicorn worker down.
        MAX_MEMBER = 512 * 1024 * 1024
        MAX_TOTAL = 1024 * 1024 * 1024
        total = 0
        scans = []
        with zipfile.ZipFile(io.BytesIO(data)) as zf:
            for info in zf.infolist():
                if info.is_dir() or info.file_size < 5000:
                    continue
                if info.file_size > MAX_MEMBER:
                    continue
                base = os.path.basename(info.filename).lower()
                if base in ("dicomdir",) or base.endswith((".jar", ".exe", ".pdf",
                                                           ".htm", ".html", ".xml",
                                                           ".css", ".png", ".txt",
                                                           ".inf", ".cmd", ".sh")):
                    continue
                # budget only what is actually inflated
                if total + info.file_size > MAX_TOTAL:
                    break
                total += info.file_size
                content = zf.read(info)
                if _is_dicom_bytes(content):
                    try:
                        scans.append(load_dicom_bytes(content, info.filename))
                    except Exception:
                        continue
        if not scans:
            raise ValueError("No readable DICOM images found in the zip archive")
        return scans

    if _is_dicom_bytes(data):
        return [load_dicom_bytes(data, source_name)]

    ext = os.path.splitext(name_lower)[1]
    if ext in IMAGE_EXTENSIONS:
        return [load_image_bytes(data, source_name)]

    # Last resort: try DICOM without preamble, then image.
    try:
        return [load_dicom_bytes(data, source_name)]
    except Exception:
        return [load_image_bytes(data, source_name)]


def load_path(path: str) -> list[ScanData]:
    with open(path, "rb") as f:
        data = f.read()
    return load_any_bytes(data, os.path.basename(path))
