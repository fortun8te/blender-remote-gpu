"""Frame encoding — converts rendered pixel buffers to JPEG/PNG/EXR for transmission."""

import io
import struct
import numpy as np

try:
    from PIL import Image
except ImportError:
    Image = None


def encode_jpeg(pixels: np.ndarray, width: int, height: int, quality: int = 75) -> bytes:
    """Encode a float32 RGBA pixel array to JPEG bytes.

    Args:
        pixels: flat float32 array of RGBA values [0, 1], length = width * height * 4
        width: image width
        height: image height
        quality: JPEG quality (1-95)

    Returns:
        JPEG bytes
    """
    if Image is None:
        raise RuntimeError("Pillow not installed")

    # Reshape to (height, width, 4)
    rgba = pixels.reshape(height, width, 4)
    # Flip vertically (Blender renders bottom-up)
    rgba = np.flipud(rgba)
    # Convert to uint8
    rgb = (rgba[:, :, :3] * 255).clip(0, 255).astype(np.uint8)

    img = Image.fromarray(rgb, "RGB")
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=quality, optimize=True)
    return buf.getvalue()


def encode_png(pixels: np.ndarray, width: int, height: int) -> bytes:
    """Encode a float32 RGBA pixel array to PNG bytes (lossless)."""
    if Image is None:
        raise RuntimeError("Pillow not installed")

    rgba = pixels.reshape(height, width, 4)
    rgba = np.flipud(rgba)
    rgba_u8 = (rgba * 255).clip(0, 255).astype(np.uint8)

    img = Image.fromarray(rgba_u8, "RGBA")
    buf = io.BytesIO()
    img.save(buf, format="PNG", compress_level=1)  # Fast compression
    return buf.getvalue()


def pixels_from_render_result(render_result) -> tuple[np.ndarray, int, int]:
    """Extract pixel data from a Blender RenderResult.

    Args:
        render_result: bpy.types.RenderResult

    Returns:
        (pixels_float32, width, height)
    """
    width = render_result.resolution_x
    height = render_result.resolution_y

    # Access the Combined pass
    layer = render_result.layers[0]
    combined = layer.passes["Combined"]

    # Get pixels as flat list
    pixel_count = width * height * 4
    pixels = np.zeros(pixel_count, dtype=np.float32)
    combined.rect.foreach_get(pixels)

    return pixels, width, height
