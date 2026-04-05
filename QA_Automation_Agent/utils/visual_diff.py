from pathlib import Path
from loguru import logger

try:
    import imagehash
    from PIL import Image
    _DEPS_AVAILABLE = True
except ImportError:
    _DEPS_AVAILABLE = False
    logger.warning("imagehash/Pillow not installed — visual diff disabled")


class VisualDiff:
    """Perceptual hash-based image comparison for regression monitoring."""

    @staticmethod
    def compare(baseline_path: str, current_path: str) -> dict:
        if not _DEPS_AVAILABLE:
            return {"similarity": 1.0, "distance": 0, "changed": False, "error": "deps_missing"}

        baseline = Path(baseline_path)
        current = Path(current_path)

        if not baseline.exists():
            return {"similarity": None, "distance": None, "changed": False, "error": "no_baseline"}

        if not current.exists():
            return {"similarity": None, "distance": None, "changed": False, "error": "no_current"}

        try:
            img1 = Image.open(baseline).convert("RGB")
            img2 = Image.open(current).convert("RGB")

            hash1 = imagehash.average_hash(img1)
            hash2 = imagehash.average_hash(img2)

            # Hamming distance: 0 = identical, max 64 = completely different
            distance = hash1 - hash2
            similarity = round((64 - distance) / 64, 4)
            changed = distance > 5  # >~8% difference threshold

            logger.debug(
                f"Visual diff: baseline={baseline.name} current={current.name} "
                f"distance={distance} similarity={similarity}"
            )

            return {
                "similarity": similarity,
                "distance": distance,
                "changed": changed,
            }
        except Exception as e:
            logger.error(f"Visual diff error: {e}")
            return {"similarity": None, "distance": None, "changed": False, "error": str(e)}

    @staticmethod
    def save_baseline(screenshot_path: str, baseline_path: str) -> bool:
        try:
            import shutil
            Path(baseline_path).parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(screenshot_path, baseline_path)
            logger.info(f"Baseline saved: {baseline_path}")
            return True
        except Exception as e:
            logger.error(f"Failed to save baseline: {e}")
            return False
