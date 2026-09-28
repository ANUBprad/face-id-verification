"""Explicit bounds on how large a decoded image may be.

The web upload limit bounds *compressed* bytes, which says nothing about how much memory
a decoded image needs. A PNG of roughly 119 KiB can legitimately declare 30000x30000 and
expand to gigabytes, so a byte limit alone does not bound resource use.

This module is the single source of truth for that policy, so the face detector, the web
input gate, and the reverse-search upload preparation cannot drift apart.

Two stages are enforced, because the container header and the pixel decoder are separate
trust boundaries and either one can disagree with the other:

1. ``enforce_declared_dimensions`` runs *before* the decode, from the header alone, so a
   hostile file is refused without ever allocating its pixel buffer.
2. ``enforce_decoded_shape`` re-checks the array that the decoder actually produced.
"""

from __future__ import annotations

import io
import logging
import warnings
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from numpy.typing import NDArray

logger = logging.getLogger(__name__)

# Sized for the actual product: a single near-frontal face for provenance verification.
#
# A modern phone photo (4032x3024, 12 MP) must be accepted, so the edges are capped well
# above any real handset sensor and the pixel budget above 12 MP. Nothing is lost by
# stopping there: InsightFace scales its input to 640x640 internally, so pixels beyond
# roughly 12 MP cannot improve detection, and every extra pixel only costs memory.
#
# The pixel cap is the binding limit, because 6000x6000 would otherwise be 36 MP.
MAX_IMAGE_WIDTH = 6000
MAX_IMAGE_HEIGHT = 6000
MAX_IMAGE_PIXELS = 16_000_000

# OpenCV decodes colour images as 3 bytes per pixel, so this is the largest buffer a single
# accepted decode may allocate. It is the same limit as MAX_IMAGE_PIXELS expressed in
# memory: at Change 14's concurrency cap of 4 that bounds image memory at roughly 192 MB,
# where before this policy the size was entirely attacker-chosen.
BYTES_PER_PIXEL = 3
MAX_DECODED_BYTES = MAX_IMAGE_PIXELS * BYTES_PER_PIXEL


class ImageResourceError(Exception):
    """Raised when an image declares or decodes to a size the policy forbids."""


def format_limit() -> str:
    """The accepted range as a noun phrase, so it can be embedded in either a rejection
    or an acceptance message without either reading awkwardly."""
    return (
        f"{MAX_IMAGE_WIDTH}x{MAX_IMAGE_HEIGHT} pixels and "
        f"{MAX_IMAGE_PIXELS // 1_000_000} megapixels in total"
    )


def _describe(width: int, height: int) -> str:
    return (
        f"the image declares {width}x{height} pixels "
        f"({width * height:,} pixels, {width * height * BYTES_PER_PIXEL / 1e6:.0f} MB decoded)"
    )


def enforce_declared_dimensions(width: int, height: int) -> tuple[int, int]:
    """Refuse declared dimensions before anything is decoded. Returns them when allowed."""
    if width <= 0 or height <= 0:
        raise ImageResourceError(
            f"The image declares an impossible size of {width}x{height} pixels."
        )
    if width > MAX_IMAGE_WIDTH:
        raise ImageResourceError(
            f"The image is {width} pixels wide, but the maximum width is {MAX_IMAGE_WIDTH}."
        )
    if height > MAX_IMAGE_HEIGHT:
        raise ImageResourceError(
            f"The image is {height} pixels tall, but the maximum height is {MAX_IMAGE_HEIGHT}."
        )
    if width * height > MAX_IMAGE_PIXELS:
        raise ImageResourceError(
            f"Too many pixels: {_describe(width, height)}. "
            f"The maximum is {format_limit()}."
        )
    return width, height


def enforce_decoded_shape(image: "NDArray") -> None:
    """Re-check the array the decoder actually produced.

    The header parser and the decoder are independent, and only one of them is right when
    a file is crafted. This runs after the allocation, so it cannot prevent it, which is
    why the declared-dimension check above exists too; its job is to catch a decoder that
    disagreed with the header it was given.
    """
    shape = getattr(image, "shape", None)
    if not shape or len(shape) < 2:
        raise ImageResourceError("The decoded image has no usable dimensions.")
    height, width = int(shape[0]), int(shape[1])
    # The pixel cap is MAX_DECODED_BYTES expressed in pixels, so checking it here bounds
    # this buffer too. It cannot prevent the allocation, only detect a lying decoder.
    enforce_declared_dimensions(width, height)


def probe_declared_size(source: bytes | Path) -> tuple[int, int] | None:
    """Read width/height from the container header without decoding any pixel.

    Returns ``None`` when the bytes are not a recognisable image, so an unreadable file
    still produces the existing decode error rather than a misleading size complaint.

    Pillow's ``Image.open`` is lazy: it parses the header and stops. That is what makes
    the preflight safe, and it is also why Pillow is used here instead of OpenCV, whose
    only entry points (``imread``, ``imdecode``, ``imcount``, ``IMREAD_REDUCED_*``) all
    allocate a buffer derived from the untrusted dimensions.
    """
    try:
        from PIL import Image, UnidentifiedImageError
    except ImportError:  # pragma: no cover - Pillow is a declared dependency
        logger.warning("Pillow is unavailable; image size cannot be checked before decoding")
        return None

    try:
        with warnings.catch_warnings():
            # Pillow warns above ~89 MP and raises above ~179 MP. Both are turned into a
            # deterministic rejection so the outcome does not depend on Pillow's
            # thresholds, and no stray warning is emitted for a hostile file.
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(_as_stream(source)) as image:
                width, height = image.size
    except (Image.DecompressionBombError, Image.DecompressionBombWarning):
        # Pillow independently refuses a file this extreme before the policy can be
        # applied to it, so the declared size is not available here. The verdict is the
        # same and the accepted range is still stated, so the message stays actionable and
        # never depends on Pillow's own thresholds being reported verbatim.
        raise ImageResourceError(
            "The image is far too large to be read safely and was refused without being "
            f"decoded. The maximum is {format_limit()}."
        ) from None
    except MemoryError:
        # A header this hostile can exhaust memory just by being parsed. Treating that as
        # "unprobeable" would let the decode through, so it is refused instead.
        raise ImageResourceError(
            "The image header could not be read within the available memory and was "
            f"refused without being decoded. The maximum is {format_limit()}."
        ) from None
    except (UnidentifiedImageError, OSError, ValueError, SyntaxError):
        return None
    except Exception as e:  # a truncated container can raise almost anything
        logger.debug("Image header could not be parsed: %s", e)
        return None

    if not isinstance(width, int) or not isinstance(height, int):
        return None
    return width, height


def _as_stream(source: bytes | Path):
    if isinstance(source, (bytes, bytearray, memoryview)):
        return io.BytesIO(bytes(source))
    return Path(source)


def check_image_bytes(data: bytes) -> tuple[int, int] | None:
    """Preflight in-memory image bytes. Raises only when the size is positively oversized."""
    size = probe_declared_size(data)
    if size is None:
        return None
    return enforce_declared_dimensions(*size)


def check_image_file(path: str | Path) -> tuple[int, int] | None:
    """Preflight an image on disk. Raises only when the size is positively oversized."""
    target = Path(path)
    if not target.is_file():
        return None
    size = probe_declared_size(target)
    if size is None:
        return None
    return enforce_declared_dimensions(*size)
