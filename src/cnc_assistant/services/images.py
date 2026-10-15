"""Saving and deleting uploaded photos (e.g. tool pictures)."""

import logging
import uuid
from pathlib import Path

from PIL import Image, ImageOps, UnidentifiedImageError

# Photos are shrunk to fit inside this box; plenty for a card, and keeps files small.
MAX_IMAGE_SIZE = (800, 800)


class ImageError(ValueError):
    """Raised when an uploaded file isn't a usable image."""


def save_image(file_storage, dest_dir: Path) -> str:
    """Validate, shrink and save an uploaded image as JPEG. Returns the new filename."""
    try:
        img = Image.open(file_storage.stream)
        img.load()
    except (UnidentifiedImageError, OSError):
        raise ImageError("That file isn't an image Pillow can read. Please upload a JPG, PNG or WebP.") from None

    img = ImageOps.exif_transpose(img)  # phone photos store rotation in EXIF; apply it
    img = img.convert("RGB")  # JPEG has no alpha channel
    img.thumbnail(MAX_IMAGE_SIZE)

    filename = f"{uuid.uuid4().hex}.jpg"
    img.save(dest_dir / filename, "JPEG", quality=85)
    return filename


def delete_image(dest_dir: Path, filename: str | None) -> None:
    """Remove a stored image if it exists. Safe to call with None.

    A file that can't be removed (e.g. locked on Windows while being served) is just
    left behind: an orphaned photo is harmless, failing the user's save is not.
    """
    if not filename:
        return
    try:
        (dest_dir / filename).unlink(missing_ok=True)
    except OSError as e:
        logging.getLogger(__name__).warning("Could not delete image %s: %s", filename, e)
