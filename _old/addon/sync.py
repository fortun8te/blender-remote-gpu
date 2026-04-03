"""Scene synchronization — serializes Blender scene state for network transfer.

Supports both full .blend file transfers and incremental delta synchronization.
Delta mode sends only changed transforms, materials, lights (~1KB) instead of
full .blend files (~100MB), reducing bandwidth by 99%+ for typical edits.
"""

import os
import tempfile
from .scene_diff import SceneStateTracker, SceneDelta


def save_blend_to_bytes() -> bytes:
    """Save the current .blend file with all assets packed for network transfer.

    IMPORTANT: External textures are packed into the .blend file.
    If textures cannot be packed, Windows rendering may fail.
    """
    import bpy

    # Save to a temp file
    tmp = os.path.join(tempfile.gettempdir(), "remote_gpu_scene.blend")

    # Check if there are external files
    has_external = False
    for image in bpy.data.images:
        if image.filepath and not image.packed_file:
            has_external = True
            break

    if has_external:
        # Try to pack all external data (textures, etc.) into the .blend
        try:
            bpy.ops.file.pack_all()
            print("[Remote GPU] All external files packed into .blend")
        except RuntimeError as e:
            # Fallback: use relative paths (Windows must have textures in same folder structure)
            print(f"[Remote GPU] WARNING: Could not pack files: {e}. External textures must be in relative paths.")
            # Make paths relative to the blend file location
            for image in bpy.data.images:
                if image.filepath:
                    try:
                        image.reload()  # Reload to update paths
                    except:
                        pass

    try:
        # Save a copy (doesn't change the user's save state)
        bpy.ops.wm.save_as_mainfile(filepath=tmp, copy=True, compress=True)
    except Exception as e:
        print(f"[Remote GPU] Failed to save .blend: {e}")
        raise

    try:
        with open(tmp, "rb") as f:
            data = f.read()
    except Exception as e:
        print(f"[Remote GPU] Failed to read saved .blend: {e}")
        raise

    # Clean up temp file
    try:
        os.unlink(tmp)
    except OSError:
        pass

    return data


def get_view_matrices(context) -> tuple[list, list, tuple]:
    """Extract current viewport camera matrices.

    Returns (view_matrix, projection_matrix, resolution).
    Matrices are serialized as flat lists of 16 floats (column-major).
    """
    region = context.region
    rv3d = context.region_data

    # Issue #1: Check if region or rv3d is None and return safe defaults
    if region is None or rv3d is None:
        return [1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1], [1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1], (1, 1)

    # View matrix: world -> camera
    view_mat = [x for col in rv3d.view_matrix for x in col]
    # Projection matrix: camera -> clip
    proj_mat = [x for col in rv3d.window_matrix for x in col]

    resolution = (region.width, region.height)

    return view_mat, proj_mat, resolution


def get_render_settings(context) -> dict:
    """Extract render settings for final render."""
    scene = context.scene
    render = scene.render
    cycles = scene.cycles if hasattr(scene, "cycles") else None

    settings = {
        "resolution_x": render.resolution_x,
        "resolution_y": render.resolution_y,
        "resolution_percentage": render.resolution_percentage,
        "film_transparent": render.film_transparent,
    }

    if cycles:
        settings.update({
            "samples": cycles.samples,
            "preview_samples": cycles.preview_samples,
            "use_denoising": cycles.use_denoising,
            "denoiser": cycles.denoiser if hasattr(cycles, "denoiser") else "OPENIMAGEDENOISE",
            "device": "GPU",  # Always GPU on server
        })

    return settings


def classify_depsgraph_updates(depsgraph) -> dict:
    """Classify depsgraph updates into categories for efficient sync.

    Returns dict with keys: 'camera', 'transforms', 'geometry', 'materials', 'full_resync'
    """
    updates = {
        "camera": False,
        "transforms": [],
        "geometry": [],
        "materials": [],
        "full_resync": False,
    }

    for update in depsgraph.updates:
        obj = update.id

        if hasattr(obj, "type"):
            if obj.type == "CAMERA":
                updates["camera"] = True
            elif update.is_updated_geometry:
                updates["geometry"].append(obj.name)
            elif update.is_updated_transform:
                updates["transforms"].append({
                    "name": obj.name,
                    "matrix": [x for col in obj.matrix_world for x in col],
                })

        # Material updates
        if hasattr(obj, "bl_rna") and obj.bl_rna.name == "Material":
            updates["materials"].append(obj.name)

    # If too many changes, just do a full resync
    total_changes = len(updates["geometry"]) + len(updates["materials"])
    if total_changes > 10:
        updates["full_resync"] = True

    return updates


# Global scene state tracker for delta synchronization
_scene_tracker: SceneStateTracker | None = None


def initialize_scene_tracker(context) -> SceneStateTracker:
    """Initialize or reset the global scene state tracker.

    Call this after sending a full scene to establish baseline for deltas.
    """
    global _scene_tracker
    _scene_tracker = SceneStateTracker()
    _scene_tracker.update(context, include_geometry=True)
    print(f"[Remote GPU] Scene tracker initialized with snapshot")
    return _scene_tracker


def get_scene_tracker() -> SceneStateTracker | None:
    """Get the current scene tracker (None if not initialized)."""
    return _scene_tracker


def clear_scene_tracker() -> None:
    """Clear delta-sync baseline; call on disconnect so reconnect forces a full scene sync."""
    global _scene_tracker
    _scene_tracker = None


def compute_scene_delta(context, max_delta_bytes: int = 1000000) -> SceneDelta | None:
    """Compute incremental scene changes since last full sync.

    Returns SceneDelta if changes detected, None if tracker not initialized.

    Args:
        context: Blender context
        max_delta_bytes: If delta exceeds this, full_resync_required is set to True

    Returns:
        SceneDelta with all changes, or None if no tracker initialized
    """
    global _scene_tracker
    if _scene_tracker is None:
        return None

    delta = _scene_tracker.compute_delta(context, max_delta_bytes=max_delta_bytes)

    if delta and not delta.full_resync_required:
        # Update tracker with new state for next delta
        _scene_tracker.update(context, include_geometry=True)

    return delta


def get_delta_metrics() -> dict:
    """Get performance metrics from scene tracker.

    Returns dict with keys:
    - deltas_computed: Number of deltas computed
    - full_resyncs: Number of times full sync was recommended
    - total_delta_bytes_sent: Total bytes of all deltas
    - total_bandwidth_saved: Estimated bytes saved vs full syncs
    - average_delta_size: Average delta size in bytes
    """
    global _scene_tracker
    if _scene_tracker is None:
        return {
            "deltas_computed": 0,
            "full_resyncs": 0,
            "total_delta_bytes_sent": 0,
            "total_bandwidth_saved": 0,
            "average_delta_size": 0,
        }
    return _scene_tracker.get_metrics()


def get_current_frame(context) -> int:
    """Extract current timeline frame number.

    Returns the frame currently playing or scrubbed to.
    """
    return context.scene.frame_current


def get_frame_range(context) -> tuple[int, int, int]:
    """Extract animation frame range and step.

    Returns (frame_start, frame_end, frame_step).
    """
    scene = context.scene
    return scene.frame_start, scene.frame_end, scene.frame_step


def get_playback_fps(context) -> float:
    """Extract render FPS and frame rate multiplier.

    Returns computed FPS value for animation playback.
    """
    render = context.scene.render
    fps = render.fps
    fps_base = render.fps_base
    return fps / fps_base if fps_base != 0 else fps
