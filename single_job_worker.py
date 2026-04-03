"""
Stateless single-job Blender worker.

This script is invoked by the dispatcher as a fresh Blender subprocess.
It receives job parameters via command-line args or environment variables,
executes the render, saves the result, and exits.

Usage:
    blender --background --python single_job_worker.py -- job_json
    where job_json is a JSON string with keys: type, params, output_path, etc.
"""

import bpy
import json
import logging
import os
import sys
import tempfile
import traceback
from pathlib import Path

# ── Setup Logging ──────────────────────────────────────

log_dir = tempfile.gettempdir()
log_file = os.path.join(log_dir, "blender_worker.log")

logging.basicConfig(
    level=logging.DEBUG,
    format='[%(asctime)s] [Worker] [%(levelname)s] %(message)s',
    datefmt='%H:%M:%S',
    handlers=[
        logging.FileHandler(log_file),
        logging.StreamHandler(),
    ]
)
log = logging.getLogger('blender_worker')

log.info("=" * 80)
log.info("Single Job Worker started")


# ── Job Execution ──────────────────────────────────────

def execute_job(job_data: dict) -> dict:
    """
    Execute a single render job.

    Args:
        job_data: Dict with keys:
            - type: "viewport_render" or "full_render"
            - blend_file: Path to .blend file
            - output_path: Where to save the PNG
            - frame: Frame number (default 1)
            - view_matrix: Optional 4x4 camera matrix
            - (other render params)

    Returns:
        Dict with status, output_path, and error info if applicable
    """
    job_type = job_data.get("type", "unknown")
    blend_file = job_data.get("blend_file", "")
    output_path = job_data.get("output_path", "")
    frame = job_data.get("frame", 1)

    log.info(f"[JOB] Executing {job_type} render")
    log.info(f"[JOB] Blend file: {blend_file}")
    log.info(f"[JOB] Output: {output_path}")

    try:
        # ── Load Blend File ────────────────────────────
        if not os.path.isfile(blend_file):
            raise FileNotFoundError(f"Blend file not found: {blend_file}")

        log.info(f"[BLEND] Loading {blend_file}...")
        bpy.ops.wm.open_mainfile(filepath=blend_file)
        log.info(f"[BLEND] Loaded successfully")

        # ── Set Frame ──────────────────────────────────
        if frame != 1:
            log.info(f"[BLEND] Setting frame to {frame}")
            bpy.context.scene.frame_set(frame)

        # ── Apply View Matrix (Camera Update) ──────────
        if "view_matrix" in job_data and job_data["view_matrix"]:
            log.info(f"[BLEND] Applying custom view matrix...")
            try:
                view_matrix = job_data["view_matrix"]
                camera = bpy.context.scene.camera
                if camera:
                    # Set camera matrix from 4x4 view matrix
                    # Typically view_matrix is row-major, convert to column-major for Blender
                    import mathutils
                    mat = mathutils.Matrix(view_matrix)
                    camera.matrix_world = mat.inverted()
                    log.info(f"[BLEND] Camera matrix applied")
            except Exception as e:
                log.warning(f"[BLEND] Could not apply view matrix: {e}")

        # ── Configure Render Output ────────────────────
        log.info(f"[RENDER] Configuring output to {output_path}")
        bpy.context.scene.render.filepath = output_path
        bpy.context.scene.render.image_settings.file_format = "PNG"

        # ── Execute Render ────────────────────────────
        log.info(f"[RENDER] Starting render...")
        bpy.ops.render.render(write_still=True)
        log.info(f"[RENDER] Render complete")

        # ── Verify Output ─────────────────────────────
        if not os.path.isfile(output_path):
            raise RuntimeError(f"Render output not found: {output_path}")

        output_size_mb = os.path.getsize(output_path) / (1024 * 1024)
        log.info(f"[RENDER] Output saved ({output_size_mb:.2f} MB)")

        return {
            "type": "render_complete",
            "status": "success",
            "output_path": output_path,
            "file_size_mb": output_size_mb,
        }

    except Exception as e:
        log.error(f"[ERROR] Job failed: {type(e).__name__}: {e}")
        log.error(f"[ERROR] Traceback:\n{traceback.format_exc()}")
        return {
            "type": "render_error",
            "status": "error",
            "error": type(e).__name__,
            "message": str(e),
        }


def main():
    """Parse job data from command line and execute."""
    log.info(f"Python sys.argv: {sys.argv}")

    # Job data can be passed as:
    # 1. Environment variable: RENDER_JOB_JSON
    # 2. Command-line argument after '--'
    job_json_str = None

    # Check environment variable first
    if "RENDER_JOB_JSON" in os.environ:
        job_json_str = os.environ["RENDER_JOB_JSON"]
        log.info("[STARTUP] Job data from environment variable")
    # Check for -- in args
    elif "--" in sys.argv:
        idx = sys.argv.index("--")
        if idx + 1 < len(sys.argv):
            job_json_str = sys.argv[idx + 1]
            log.info("[STARTUP] Job data from command-line argument")

    if not job_json_str:
        log.error("[STARTUP] No job data provided (set RENDER_JOB_JSON or pass after --)")
        sys.exit(1)

    # ── Parse Job Data ────────────────────────────────
    try:
        job_data = json.loads(job_json_str)
        log.info(f"[STARTUP] Job parsed successfully: type={job_data.get('type', '?')}")
    except json.JSONDecodeError as e:
        log.error(f"[STARTUP] Invalid JSON: {e}")
        log.error(f"[STARTUP] Job string: {job_json_str[:200]}")
        sys.exit(1)

    # ── Execute Job ────────────────────────────────────
    result = execute_job(job_data)
    log.info(f"[RESULT] {json.dumps(result)}")

    # ── Cleanup & Exit ────────────────────────────────
    sys.exit(0 if result.get("status") == "success" else 1)


if __name__ == "__main__":
    main()
