"""Scene management — handles .blend files and incremental updates on the server."""

import os
import tempfile
import logging

logger = logging.getLogger("remote-gpu.scene")


class SceneManager:
    """Manages the current Blender scene on the render server.

    Handles:
    - Receiving and loading full .blend files
    - Applying incremental deltas (transforms, materials)
    - Camera updates for viewport rendering
    """

    def __init__(self, work_dir: str | None = None):
        self.work_dir = work_dir or os.path.join(tempfile.gettempdir(), "remote_gpu_server")
        os.makedirs(self.work_dir, exist_ok=True)
        self.current_blend: str | None = None
        self.scene_loaded = False

    def receive_full_scene(self, blend_data: bytes) -> str:
        """Save received .blend data to disk and load it.

        Issue #7: Validate .blend file exists and is readable before loading.
        Returns the path to the saved .blend file.
        """
        blend_path = os.path.join(self.work_dir, "scene.blend")

        try:
            with open(blend_path, "wb") as f:
                f.write(blend_data)

            # Issue #7: Validate file exists and is readable
            if not os.path.exists(blend_path):
                raise RuntimeError(f"Failed to write .blend file: {blend_path}")

            if not os.access(blend_path, os.R_OK):
                raise PermissionError(f"Cannot read .blend file after write: {blend_path}")

            logger.info(f"Received scene: {len(blend_data) / 1024 / 1024:.1f} MB -> {blend_path}")
            self.current_blend = blend_path
            self.scene_loaded = True

            return blend_path

        except IOError as e:
            logger.error(f"I/O error writing .blend file: {e}")
            raise
        except Exception as e:
            logger.error(f"Failed to receive scene: {e}")
            raise

    def load_scene_into_blender(self):
        """Load the current .blend file into the running Blender instance."""
        import bpy

        if self.current_blend is None:
            raise RuntimeError("No scene received yet")

        bpy.ops.wm.open_mainfile(filepath=self.current_blend)

        # Force GPU rendering
        scene = bpy.context.scene
        if hasattr(scene, "cycles"):
            scene.cycles.device = "GPU"

        # Enable all CUDA/OptiX devices
        prefs = bpy.context.preferences.addons.get("cycles")
        if prefs:
            cprefs = prefs.preferences
            cprefs.compute_device_type = "OPTIX"  # RTX 5090 -> OptiX
            for device in cprefs.devices:
                device.use = True

        logger.info(f"Scene loaded: {self.current_blend}")

    def apply_camera_update(self, view_matrix: list, proj_matrix: list, resolution: tuple):
        """Apply a viewport camera update.

        Creates/updates a temporary camera object that matches the viewport.
        """
        import bpy
        import mathutils

        scene = bpy.context.scene

        # Reconstruct the 4x4 view matrix
        mat = mathutils.Matrix([
            view_matrix[0:4],
            view_matrix[4:8],
            view_matrix[8:12],
            view_matrix[12:16],
        ])
        # View matrix is world->camera, we need camera->world (inverted)
        cam_matrix = mat.inverted()

        # Get or create the render camera
        cam_name = "__remote_viewport_cam"
        if cam_name not in bpy.data.objects:
            cam_data = bpy.data.cameras.new(cam_name)
            cam_obj = bpy.data.objects.new(cam_name, cam_data)
            scene.collection.objects.link(cam_obj)
        else:
            cam_obj = bpy.data.objects[cam_name]

        cam_obj.matrix_world = cam_matrix
        scene.camera = cam_obj

        # Update resolution
        scene.render.resolution_x = resolution[0]
        scene.render.resolution_y = resolution[1]
        scene.render.resolution_percentage = 100

    def apply_delta(self, delta: dict) -> dict:
        """Apply incremental scene changes.

        Processes delta dict with structure:
        {
            "modified_objects": [{
                "name": str,
                "type": str,
                "changes": {
                    "transform": [16 floats],
                    "geometry": bool,
                    "material_N": {...} or bool,
                    "light": {...},
                    "camera": {...},
                }
            }],
            "deleted_objects": [str],
            "new_objects": [str],
            "full_resync_required": bool,
        }

        Returns dict with status:
        {
            "success": bool,
            "objects_updated": int,
            "errors": [str],
            "requires_full_resync": bool,
        }
        """
        import bpy
        import mathutils

        result = {
            "success": True,
            "objects_updated": 0,
            "errors": [],
            "requires_full_resync": False,
        }

        # Check if delta itself signals full resync needed
        if delta.get("full_resync_required"):
            logger.info("Delta marked full_resync_required by client")
            result["requires_full_resync"] = True
            return result

        try:
            # Apply modified objects
            for obj_delta in delta.get("modified_objects", []):
                obj_name = obj_delta.get("name")
                changes = obj_delta.get("changes", {})

                obj = bpy.data.objects.get(obj_name)
                if not obj:
                    result["errors"].append(f"Object not found: {obj_name}")
                    continue

                # Apply transform
                if "transform" in changes:
                    try:
                        matrix_floats = changes["transform"]
                        mat = mathutils.Matrix([
                            matrix_floats[0:4],
                            matrix_floats[4:8],
                            matrix_floats[8:12],
                            matrix_floats[12:16],
                        ])
                        obj.matrix_world = mat
                        logger.debug(f"Applied transform to {obj_name}")
                    except Exception as e:
                        result["errors"].append(f"Transform failed for {obj_name}: {e}")

                # Apply light changes
                if "light" in changes and obj.type == "LIGHT":
                    try:
                        light_data = changes["light"]
                        light = obj.data
                        if "energy" in light_data and hasattr(light, "energy"):
                            light.energy = light_data["energy"]
                        if "color" in light_data and hasattr(light, "color"):
                            light.color = light_data["color"]
                        logger.debug(f"Applied light changes to {obj_name}")
                    except Exception as e:
                        result["errors"].append(f"Light update failed for {obj_name}: {e}")

                # Apply camera changes
                if "camera" in changes and obj.type == "CAMERA":
                    try:
                        cam_data = changes["camera"]
                        cam = obj.data
                        if "lens" in cam_data and hasattr(cam, "lens"):
                            cam.lens = cam_data["lens"]
                        logger.debug(f"Applied camera changes to {obj_name}")
                    except Exception as e:
                        result["errors"].append(f"Camera update failed for {obj_name}: {e}")

                # Geometry or material changes require more complex handling
                # For now, these are flagged and a full resync is recommended
                if "geometry" in changes:
                    logger.info(f"Geometry change detected for {obj_name} — recommend full resync")
                    result["requires_full_resync"] = True

                for key in changes:
                    if key.startswith("material_"):
                        logger.info(f"Material change detected for {obj_name} — recommend full resync")
                        result["requires_full_resync"] = True
                        break

                result["objects_updated"] += 1

            # Handle deleted objects
            for obj_name in delta.get("deleted_objects", []):
                obj = bpy.data.objects.get(obj_name)
                if obj:
                    try:
                        bpy.data.objects.remove(obj, do_unlink=True)
                        result["objects_updated"] += 1
                        logger.debug(f"Deleted object {obj_name}")
                    except Exception as e:
                        result["errors"].append(f"Delete failed for {obj_name}: {e}")

            # New objects without full scene data would need to be created
            # This is a limitation of delta-only — for now we recommend full resync
            if delta.get("new_objects"):
                logger.info(f"New objects detected — recommend full resync")
                result["requires_full_resync"] = True

        except Exception as e:
            logger.error(f"Error applying delta: {e}", exc_info=True)
            result["success"] = False
            result["errors"].append(str(e))
            result["requires_full_resync"] = True

        return result

    def get_blend_path(self) -> str | None:
        """Get the path to the current .blend file."""
        return self.current_blend
