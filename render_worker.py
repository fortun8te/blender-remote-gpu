#!/usr/bin/env python3
"""
Single-Job Blender Render Worker (Agent R2 rewrite)

This worker is invoked as a FRESH Blender subprocess for each render job.
No persistent state, no HTTP server, no polling — just:
  1. Start fresh
  2. Load ONE scene
  3. Render ONE frame
  4. Exit clean

Job data is passed via RENDER_JOB_JSON environment variable.

Timeout: Dispatcher kills subprocess after 5 minutes.
Output: Job prints JSON result to stdout on success, exit(0) on success, exit(1) on error.
Logging: All diagnostics to /tmp/blender_worker.log with rotation.
"""

import bpy
import json
import logging
import os
import sys
import tempfile
import traceback
import uuid
from pathlib import Path
from logging.handlers import RotatingFileHandler

# ── Setup Logging ──────────────────────────────────────
log_dir = tempfile.gettempdir()
log_file = os.path.join(log_dir, "blender_worker.log")

logger = logging.getLogger('blender_worker')
logger.setLevel(logging.DEBUG)

# Rotating file handler (10MB per file, keep 5 backups)
try:
    file_handler = RotatingFileHandler(log_file, maxBytes=10*1024*1024, backupCount=5)
    file_handler.setFormatter(logging.Formatter('[%(asctime)s] [%(levelname)s] %(message)s', datefmt='%H:%M:%S'))
    logger.addHandler(file_handler)
except Exception as e:
    print(f"[LOGGING_ERROR] Could not setup file logging: {e}", flush=True)

# Also log to console for visibility
console_handler = logging.StreamHandler()
console_handler.setFormatter(logging.Formatter('[%(asctime)s] [%(levelname)s] %(message)s', datefmt='%H:%M:%S'))
logger.addHandler(console_handler)

logger.info("=" * 80)
logger.info("Single-Job Render Worker started (Agent R2 rewrite)")


# ── Job Execution ──────────────────────────────────────

def execute_job(job_data: dict) -> dict:
    """
    Execute a single render job in isolation.

    Job schema:
    {
        "type": "render",
        "scene_path": "/path/to/file.blend",
        "width": 640,
        "height": 360,
        "samples": 4,
        "frame": 1,
        "output_dir": "/tmp or specified directory"
    }

    Returns dict with:
    {
        "status": "success" | "error",
        "output_path": "path/to/result.png" (on success),
        "error": "error type" (on failure),
        "message": "error message" (on failure),
        "file_size_mb": 0.5 (on success)
    }
    """

    job_type = job_data.get("type", "unknown")
    scene_path = job_data.get("scene_path", "")
    width = job_data.get("width", 640)
    height = job_data.get("height", 360)
    samples = job_data.get("samples", 1)
    frame = job_data.get("frame", 1)
    output_dir = job_data.get("output_dir", tempfile.gettempdir())

    logger.info(f"[JOB] Executing {job_type} render job")
    logger.info(f"[JOB] Scene: {scene_path}")
    logger.info(f"[JOB] Resolution: {width}x{height}, Samples: {samples}, Frame: {frame}")

    try:
        # ── Validate Scene Path ────────────────────────────
        if not scene_path:
            raise ValueError("No scene_path provided in job")

        if not os.path.isfile(scene_path):
            raise FileNotFoundError(f"Scene file not found: {scene_path}")

        logger.info(f"[LOAD] Scene file exists: {os.path.getsize(scene_path) / (1024*1024):.2f} MB")

        # ── Load Scene ─────────────────────────────────────
        logger.info(f"[LOAD] Opening {scene_path}...")
        try:
            bpy.ops.wm.open_mainfile(filepath=scene_path)
        except Exception as e:
            raise RuntimeError(f"Failed to open scene file: {e}")

        logger.info(f"[LOAD] Scene loaded successfully")

        # ── Set Frame ──────────────────────────────────────
        if frame != 1:
            logger.info(f"[SCENE] Setting frame to {frame}")
            try:
                bpy.context.scene.frame_set(frame)
            except Exception as e:
                logger.warning(f"[SCENE] Could not set frame: {e}")

        # ── Configure Render Settings ──────────────────────
        logger.info(f"[RENDER] Configuring render settings")

        # Set resolution
        bpy.context.scene.render.resolution_x = width
        bpy.context.scene.render.resolution_y = height
        logger.info(f"[RENDER] Resolution set to {width}x{height}")

        # Set samples (if using Cycles engine)
        if bpy.context.scene.render.engine == 'CYCLES':
            bpy.context.scene.cycles.samples = samples
            logger.info(f"[RENDER] Cycles samples set to {samples}")

        # ── Generate Output Path ───────────────────────────
        os.makedirs(output_dir, exist_ok=True)
        result_id = str(uuid.uuid4())[:8]
        output_path = os.path.join(output_dir, f"blender_result_{result_id}.png")

        logger.info(f"[RENDER] Output path: {output_path}")
        bpy.context.scene.render.filepath = output_path
        bpy.context.scene.render.image_settings.file_format = "PNG"
        bpy.context.scene.render.image_settings.color_mode = "RGB"

        # ── Execute Render ─────────────────────────────────
        logger.info(f"[RENDER] Starting render...")
        try:
            bpy.ops.render.render(write_still=True)
        except Exception as e:
            raise RuntimeError(f"Render failed: {e}")

        logger.info(f"[RENDER] Render complete")

        # ── Verify Output ──────────────────────────────────
        if not os.path.isfile(output_path):
            raise RuntimeError(f"Render output file not created: {output_path}")

        output_size_mb = os.path.getsize(output_path) / (1024 * 1024)
        logger.info(f"[RENDER] Output verified: {output_size_mb:.2f} MB")

        return {
            "status": "success",
            "output_path": output_path,
            "file_size_mb": output_size_mb,
            "result_id": result_id,
        }

    except Exception as e:
        logger.error(f"[ERROR] Job failed: {type(e).__name__}: {e}")
        logger.error(f"[ERROR] Traceback:\n{traceback.format_exc()}")

        return {
            "status": "error",
            "error": type(e).__name__,
            "message": str(e),
        }


def main():
    """
    Main entry point: parse job data from environment and execute.

    Job data source (in priority order):
    1. RENDER_JOB_JSON environment variable
    2. Command-line argument after '--'

    On success: Print JSON result, exit(0)
    On error: Print JSON error, exit(1)
    """

    logger.info(f"Python argv: {sys.argv}")

    job_json_str = None

    # Priority 1: Environment variable
    if "RENDER_JOB_JSON" in os.environ:
        job_json_str = os.environ["RENDER_JOB_JSON"]
        logger.info("[STARTUP] Job data from RENDER_JOB_JSON environment variable")

    # Priority 2: Command-line argument after '--'
    elif "--" in sys.argv:
        try:
            idx = sys.argv.index("--")
            if idx + 1 < len(sys.argv):
                job_json_str = sys.argv[idx + 1]
                logger.info("[STARTUP] Job data from command-line argument")
        except Exception as e:
            logger.warning(f"[STARTUP] Could not parse command-line args: {e}")

    # No job data found
    if not job_json_str:
        error_msg = "No job data provided (set RENDER_JOB_JSON env var or pass after --)"
        logger.error(f"[STARTUP] {error_msg}")
        print(json.dumps({
            "status": "error",
            "error": "NoJobData",
            "message": error_msg,
        }))
        sys.exit(1)

    # ── Parse Job Data ────────────────────────────────────
    try:
        job_data = json.loads(job_json_str)
        logger.info(f"[STARTUP] Job parsed: type={job_data.get('type', '?')}")
    except json.JSONDecodeError as e:
        error_msg = f"Invalid JSON in job data: {e}"
        logger.error(f"[STARTUP] {error_msg}")
        logger.error(f"[STARTUP] First 200 chars: {job_json_str[:200]}")
        print(json.dumps({
            "status": "error",
            "error": "JSONDecodeError",
            "message": error_msg,
        }))
        sys.exit(1)

    # ── Execute Job ────────────────────────────────────────
    result = execute_job(job_data)

    # Print JSON result to stdout (dispatcher reads this)
    print(json.dumps(result))

    # ── Exit ───────────────────────────────────────────────
    exit_code = 0 if result.get("status") == "success" else 1
    logger.info(f"[EXIT] Exiting with code {exit_code}")
    sys.exit(exit_code)


if __name__ == "__main__":
    main()
