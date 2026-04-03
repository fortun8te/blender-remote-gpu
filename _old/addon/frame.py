"""Frame decoding and GPU texture management for viewport display."""

import io
import numpy as np

try:
    from PIL import Image
except ImportError:
    Image = None


def decode_jpeg(data: bytes) -> tuple[np.ndarray, int, int]:
    """Decode JPEG bytes to a float32 RGBA numpy array.

    Returns (pixels, width, height).
    pixels is shaped (height, width, 4) — correct for GPU texture creation.
    """
    if Image is None:
        raise RuntimeError("Pillow not installed")

    try:
        img = Image.open(io.BytesIO(data))
        # Validate JPEG before decode — catch corrupted frames early
        img.verify()
        # Re-open after verify (verify closes the file)
        img = Image.open(io.BytesIO(data))
        img = img.convert("RGBA")
        width, height = img.size

        # Validate max frame size to prevent memory exhaustion
        max_pixels = 16384 * 16384
        if width * height > max_pixels:
            raise ValueError(f"Frame too large: {width}x{height} exceeds {max_pixels} pixels")

        # Convert to float32 [0, 1] range — Blender GPU textures expect this
        pixels = np.array(img, dtype=np.float32) / 255.0
        # Flip vertically — Blender's GL coordinate system is bottom-up
        pixels = np.flipud(pixels)
        # Keep as (height, width, 4) — Blender GPU expects this shape
        pixels = pixels.reshape(height, width, 4)

        # Validate shape explicitly
        if pixels.shape != (height, width, 4):
            raise ValueError(f"Invalid pixel array shape: {pixels.shape}, expected ({height}, {width}, 4)")

        return pixels, width, height
    except Exception as e:
        import logging
        logging.error(f"JPEG decode failed: {e}")
        raise RuntimeError(f"JPEG decoding failed: {e}")


def decode_exr(data: bytes) -> tuple[np.ndarray, int, int, dict]:
    """Decode multilayer EXR bytes to float32 RGBA beauty pass + metadata.

    Returns (pixels, width, height, pass_data) where:
    - pixels: beauty pass as float32 RGBA (lossless, HDR)
    - pass_data: dict with all available render passes (normal, depth, etc.)

    For final renders — preserves all AOVs/render passes for local compositor.
    """
    import logging

    try:
        import OpenEXR
        import Imath
    except ImportError:
        # Fallback to simple error instead of half-implemented PIL decode
        logging.error("OpenEXR not installed — required for EXR decoding")
        raise RuntimeError("OpenEXR not installed. Install via: pip install OpenEXR")

    # Full OpenEXR parsing with all passes
    try:
        exr_file = OpenEXR.InputFile(io.BytesIO(data))
        header = exr_file.header()

        width = header['displayWindow'].max.x + 1
        height = header['displayWindow'].max.y + 1

        channels = header['channels']
        pass_data = {}

        # Extract all available channels/passes
        for channel_name in channels.keys():
            try:
                channel_data = exr_file.channel(channel_name, Imath.PixelType.FLOAT)
                arr = np.frombuffer(channel_data, dtype=np.float32)
                arr = arr.reshape((height, width))
                pass_data[channel_name] = arr
            except Exception as e:
                import logging
                logging.warning(f"Failed to extract EXR channel {channel_name}: {e}")
                continue

        # Extract beauty pass (RGB or RGBA)
        beauty_pass = None
        if 'RGBA' in pass_data:
            # Has alpha
            beauty_pass = pass_data['RGBA']
        elif 'RGB' in pass_data:
            # Add alpha channel
            rgb = pass_data['RGB']
            alpha = np.ones((height, width), dtype=np.float32)
            beauty_pass = np.stack([rgb[..., i] for i in range(3)] + [alpha], axis=-1)
        elif set(['R', 'G', 'B', 'A']).issubset(pass_data.keys()):
            # Individual channels
            beauty_pass = np.stack([
                pass_data['R'],
                pass_data['G'],
                pass_data['B'],
                pass_data.get('A', np.ones((height, width), dtype=np.float32))
            ], axis=-1)
        else:
            # Fallback: use first 3 channels as RGB
            keys = list(pass_data.keys())[:3]
            if len(keys) >= 3:
                stacked = np.stack([pass_data[k] for k in keys], axis=-1)
                alpha = np.ones((height, width, 1), dtype=np.float32)
                beauty_pass = np.concatenate([stacked, alpha], axis=-1)
            else:
                raise ValueError("EXR has no standard RGB/RGBA data")

        # Flip vertically for Blender GL coordinate system (bottom-up)
        pixels = np.flipud(beauty_pass)
        pixels = pixels.reshape(height, width, 4)

        return pixels, width, height, pass_data

    except Exception as e:
        import logging
        logging.error(f"Failed to parse EXR: {e}")
        raise RuntimeError(f"EXR decoding failed: {e}")
