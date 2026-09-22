"""Turning a MONOCHROME1 image the right way up, the same way every time.

X-ray images store brightness in one of two conventions. MONOCHROME2: a bigger
number is brighter. MONOCHROME1: a bigger number is darker — which is what the
field site's Fuji CR writes. The software flips MONOCHROME1 so every image is
analysed the same way.

It used to flip about the brightest pixel in each image. That equals the
detector's maximum only when some pixel reaches it. On every readable field
scan one does — the unblocked beam at the image edge saturates the 10-bit Fuji
at 1023, on 77,000 to 100,000 pixels — so nothing measured so far changes. The
two exposures where the rules differ are the two completely faulty ones.

What the change guards against is the next exposure where nothing reaches the
maximum: collimated inside the phantom, or at a lower dose. Every value would
then shift by an amount set by the picture's content, moving the uniformity
signal-to-noise and the wedge ratio from one scan to the next for no physical
reason.
"""

from __future__ import annotations

import io

import numpy as np
import pytest

pydicom = pytest.importorskip("pydicom")

from phantom_qa import ingest  # noqa: E402


def _dicom(stored, *, bits=10, signed=False, photometric="MONOCHROME1",
           slope=None, intercept=None, drop=()):
    """A minimal DICOM holding exactly these stored values."""
    from pydicom.dataset import Dataset, FileMetaDataset
    from pydicom.uid import ExplicitVRLittleEndian, generate_uid

    stored = np.asarray(stored)
    ds = Dataset()
    ds.file_meta = FileMetaDataset()
    ds.file_meta.TransferSyntaxUID = ExplicitVRLittleEndian
    ds.file_meta.MediaStorageSOPClassUID = "1.2.840.10008.5.1.4.1.1.1"
    uid = generate_uid()
    ds.file_meta.MediaStorageSOPInstanceUID = uid
    ds.SOPInstanceUID = uid
    ds.SOPClassUID = "1.2.840.10008.5.1.4.1.1.1"
    ds.Modality = "CR"
    ds.PhotometricInterpretation = photometric
    ds.SamplesPerPixel = 1
    ds.Rows, ds.Columns = stored.shape
    ds.BitsAllocated = 16
    ds.BitsStored = bits
    ds.HighBit = bits - 1
    ds.PixelRepresentation = 1 if signed else 0
    if slope is not None:
        ds.RescaleSlope = slope
    if intercept is not None:
        ds.RescaleIntercept = intercept
    ds.PixelData = stored.astype(np.int16 if signed else np.uint16).tobytes()
    for keyword in drop:
        delattr(ds, keyword)
    buf = io.BytesIO()
    pydicom.dcmwrite(buf, ds, enforce_file_format=True)
    return buf.getvalue()


def _load(data):
    return ingest.load_dicom_bytes(data, "test.dcm")


def _scene(brightest):
    """The same object, differing only in its single brightest pixel."""
    a = np.full((32, 32), 400, dtype=np.int64)
    a[8:16, 8:16] = 700
    a[0, 0] = brightest
    return a


# ------------------------------------------------------ the flip itself

def test_the_flip_uses_the_detector_range_not_the_brightest_pixel():
    """A 10-bit detector runs 0..1023. A pixel stored as 400 is 623 turned
    the right way up, whatever else happens to be in the picture."""
    scan = _load(_dicom(_scene(brightest=900), bits=10))
    assert scan.pixels[20, 20] == 1023 - 400
    assert scan.pixels[10, 10] == 1023 - 700


def test_the_same_object_gives_the_same_values_whatever_the_brightest_pixel():
    """The whole point. Under the old rule these two differed by 123 on every
    pixel, because one stray bright pixel moved the pivot."""
    a = _load(_dicom(_scene(brightest=900), bits=10)).pixels
    b = _load(_dicom(_scene(brightest=1023), bits=10)).pixels
    assert np.array_equal(a[1:, 1:], b[1:, 1:])


def test_an_image_that_reaches_the_maximum_comes_out_exactly_as_before():
    """Every readable field scan is in this case, which is why nothing
    measured so far changes."""
    stored = _scene(brightest=1023)
    new = _load(_dicom(stored, bits=10)).pixels
    old = float(stored.max()) - stored
    assert np.array_equal(new, old)


def test_monochrome2_is_left_alone():
    stored = _scene(brightest=900)
    scan = _load(_dicom(stored, bits=10, photometric="MONOCHROME2"))
    assert np.array_equal(scan.pixels, stored.astype(float))
    assert "_inversion" not in scan.meta


def test_the_rescale_is_carried_through_the_flip():
    """The flip is of the stored value; slope and intercept then apply to
    the result exactly as they would to any other pixel."""
    stored = _scene(brightest=900)
    scan = _load(_dicom(stored, bits=12, slope=2.0, intercept=-50.0))
    expected = ((0 + 4095) - stored) * 2.0 - 50.0
    assert np.allclose(scan.pixels, expected)


def test_a_signed_detector_range_is_respected():
    stored = np.full((16, 16), -100, dtype=np.int64)
    stored[4:8, 4:8] = 300
    scan = _load(_dicom(stored, bits=12, signed=True))
    lo, hi = -2048, 2047
    assert np.array_equal(scan.pixels, (lo + hi) - stored)


# ------------------------------------------------------ saying what was done

def test_the_record_says_how_the_image_was_turned_up():
    """So a value that looks odd later can be traced to it."""
    scan = _load(_dicom(_scene(brightest=900), bits=10))
    assert scan.meta["_inversion"]["method"] == "bit depth"
    assert scan.meta["_inversion"]["bits"] == 10
    assert scan.meta["_inversion"]["range"] == [0, 1023]


def test_values_outside_the_declared_range_fall_back_and_say_why():
    """A header that lies about its bit depth would make the new rule produce
    negative values; the old rule is the safer answer, and the record says it
    was used."""
    from types import SimpleNamespace
    stored = _scene(brightest=3000).astype(float)
    ds = SimpleNamespace(BitsStored=10, PixelRepresentation=0)
    values, note = ingest._invert_monochrome1(stored.copy(), stored, ds, 1.0, 0.0)
    assert note["method"] == "image maximum"
    assert "outside" in note["reason"]
    assert np.array_equal(values, 3000.0 - stored)


def test_no_declared_bit_depth_falls_back_and_says_why():
    from types import SimpleNamespace
    stored = _scene(brightest=900).astype(float)
    values, note = ingest._invert_monochrome1(stored.copy(), stored,
                                              SimpleNamespace(), 1.0, 0.0)
    assert note["method"] == "image maximum"
    assert "no bit depth" in note["reason"]
    assert values.min() == 0.0


def test_bits_allocated_stands_in_when_bits_stored_is_missing():
    from types import SimpleNamespace
    stored = _scene(brightest=900).astype(float)
    ds = SimpleNamespace(BitsAllocated=16, PixelRepresentation=0)
    values, note = ingest._invert_monochrome1(stored.copy(), stored, ds, 1.0, 0.0)
    assert note["method"] == "bit depth" and note["bits"] == 16
    assert values[20, 20] == 65535 - 400


# ------------------------------------------------------ the real field scans

def test_the_readable_field_scans_are_unchanged_to_the_last_pixel():
    """Checked, not tuned: the field scans are never used to set anything.

    Of the five MONOCHROME1 exposures from the field, three are readable and
    all three reach 1023 somewhere, so the new rule must reproduce the old one
    exactly. The other two are the completely faulty over-ranged pair, the
    only place the two rules ever disagreed."""
    from field_scans import INVENTORY, available, path_of
    readable = [e for e in INVENTORY if e["defect"] in (None, "clipped")]
    if not all(available(e) for e in readable):
        pytest.skip("field-test scans not present")
    for e in readable:
        raw = pydicom.dcmread(path_of(e)).pixel_array.astype(float)
        new = ingest.load_path(path_of(e))[0].pixels
        assert np.array_equal(new, raw.max() - raw), e["key"]


def test_only_the_faulty_field_scans_move_and_by_how_much():
    from field_scans import INVENTORY, available, path_of
    faulty = [e for e in INVENTORY if e["defect"] == "saturated"]
    if not all(available(e) for e in faulty):
        pytest.skip("field-test scans not present")
    shifts = []
    for e in faulty:
        raw = pydicom.dcmread(path_of(e)).pixel_array.astype(float)
        new = ingest.load_path(path_of(e))[0].pixels
        shift = np.unique(new - (raw.max() - raw))
        assert shift.size == 1, "the change must be a single constant offset"
        shifts.append(int(shift[0]))
    assert sorted(shifts) == [66, 95]
