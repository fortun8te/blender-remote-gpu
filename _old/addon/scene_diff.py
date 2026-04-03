"""Scene state diffing — tracks and detects incremental changes to Blender scenes.

This module enables delta synchronization, sending only changed data instead of
full .blend files. Typical savings: 100MB .blend file → 1-10KB delta.

Main components:
- SceneSnapshot: Captures hashable representation of scene state
- SceneDiff: Compares snapshots and generates minimal deltas
- DeltaSerializer: Converts deltas to network-portable format
"""

import hashlib
import json
import logging
from dataclasses import dataclass, asdict, field
from typing import Optional, Any
from enum import Enum


logger = logging.getLogger("remote-gpu.scene_diff")


class ChangeType(Enum):
    """Categorizes what changed in a scene."""
    TRANSFORM = "transform"          # Object position/rotation/scale
    GEOMETRY = "geometry"             # Mesh data changed
    MATERIAL = "material"             # Material node or property changed
    LIGHT = "light"                   # Light intensity/color/type
    CAMERA = "camera"                 # Camera settings or position
    TEXTURE = "texture"               # Texture assignment or parameter
    PROPERTY = "property"             # Custom property or setting


@dataclass
class ObjectSnapshot:
    """Minimal representation of object state for diffing."""
    name: str
    type: str                         # MESH, LIGHT, CAMERA, etc.
    transform_hash: str               # Hash of matrix_world (16 floats)
    geometry_hash: Optional[str]      # Hash of mesh data (if mesh)
    material_hashes: dict[str, str]   # {slot_index: material_hash}
    light_hash: Optional[str]         # Hash of light properties
    camera_hash: Optional[str]        # Hash of camera properties
    custom_props_hash: str            # Hash of custom properties
    visible: bool


@dataclass
class SceneSnapshot:
    """Complete hashable snapshot of scene state."""
    scene_name: str
    objects: dict[str, ObjectSnapshot]  # {obj_name: ObjectSnapshot}
    global_hash: str                     # Single hash of entire snapshot

    def to_dict(self) -> dict:
        """Convert to JSON-serializable format."""
        return {
            "scene_name": self.scene_name,
            "objects": {
                name: asdict(snap) for name, snap in self.objects.items()
            },
            "global_hash": self.global_hash,
        }


@dataclass
class ObjectDelta:
    """Changes to a single object."""
    name: str
    type: str
    changes: dict[str, Any] = field(default_factory=dict)
    # E.g. {
    #   "transform": [16 floats],
    #   "material_0": {"nodes": {...}, "links": [...]},
    #   "visibility": True,
    # }

    def has_changes(self) -> bool:
        return bool(self.changes)


@dataclass
class SceneDelta:
    """Minimal set of changes from previous snapshot."""
    timestamp: float
    modified_objects: list[ObjectDelta]
    deleted_objects: list[str]
    new_objects: list[str]
    full_resync_required: bool = False
    bandwidth_saved_bytes: int = 0

    def to_dict(self) -> dict:
        """Serialize to JSON-compatible format."""
        return {
            "timestamp": self.timestamp,
            "modified_objects": [asdict(od) for od in self.modified_objects],
            "deleted_objects": self.deleted_objects,
            "new_objects": self.new_objects,
            "full_resync_required": self.full_resync_required,
            "bandwidth_saved_bytes": self.bandwidth_saved_bytes,
        }

    def size_estimate(self) -> int:
        """Estimate msgpack-serialized size in bytes."""
        import msgpack
        try:
            return len(msgpack.packb(self.to_dict(), use_bin_type=True))
        except Exception:
            return 10000  # Conservative fallback


class SceneDiffer:
    """Compares Blender scene snapshots and detects changes."""

    @staticmethod
    def _hash_matrix_world(matrix) -> str:
        """Hash a 4x4 matrix (object transform).

        Rounds to 5 decimal places to avoid floating-point noise.
        """
        # Flatten and round to reduce noise
        floats = [round(x, 5) for col in matrix for x in col]
        data = json.dumps(floats).encode("utf-8")
        return hashlib.sha256(data).hexdigest()[:16]

    @staticmethod
    def _hash_mesh_data(obj) -> Optional[str]:
        """Hash mesh geometry (vertices, edges, faces).

        Quick approximation: hash vertex count + edge count + face count.
        More detailed hashing would require MD5 of all vertex coordinates.
        """
        if not hasattr(obj, "data") or not hasattr(obj.data, "vertices"):
            return None
        mesh = obj.data
        data = json.dumps({
            "vcount": len(mesh.vertices),
            "ecount": len(mesh.edges),
            "fcount": len(mesh.polygons),
        }).encode("utf-8")
        return hashlib.sha256(data).hexdigest()[:16]

    @staticmethod
    def _hash_material_properties(material) -> str:
        """Hash material node tree and properties."""
        if not hasattr(material, "node_tree"):
            return "none"

        node_data = []
        try:
            for node in material.node_tree.nodes:
                node_info = {
                    "type": node.type,
                    "inputs": {}
                }
                # Hash input socket values
                for inp in node.inputs:
                    if hasattr(inp, "default_value"):
                        val = inp.default_value
                        # Handle different types
                        if isinstance(val, (int, float)):
                            node_info["inputs"][inp.name] = round(val, 5)
                        elif isinstance(val, tuple):
                            node_info["inputs"][inp.name] = [round(x, 5) for x in val]
                        else:
                            node_info["inputs"][inp.name] = str(val)
                node_data.append(node_info)
        except Exception as e:
            logger.warning(f"Error hashing material {material.name}: {e}")
            return "error"

        data = json.dumps(node_data, sort_keys=True).encode("utf-8")
        return hashlib.sha256(data).hexdigest()[:16]

    @staticmethod
    def _hash_light_properties(obj) -> Optional[str]:
        """Hash light-specific properties (energy, color, type)."""
        if obj.type != "LIGHT" or not hasattr(obj, "data"):
            return None

        light = obj.data
        data = {
            "type": light.type,
            "energy": round(light.energy, 5) if hasattr(light, "energy") else None,
            "color": tuple(round(x, 5) for x in light.color) if hasattr(light, "color") else None,
        }
        serialized = json.dumps(data, sort_keys=True).encode("utf-8")
        return hashlib.sha256(serialized).hexdigest()[:16]

    @staticmethod
    def _hash_camera_properties(obj) -> Optional[str]:
        """Hash camera settings (lens, sensor_width, etc.)."""
        if obj.type != "CAMERA" or not hasattr(obj, "data"):
            return None

        cam = obj.data
        data = {
            "lens": round(cam.lens, 5) if hasattr(cam, "lens") else None,
            "sensor_width": round(cam.sensor_width, 5) if hasattr(cam, "sensor_width") else None,
            "sensor_height": round(cam.sensor_height, 5) if hasattr(cam, "sensor_height") else None,
        }
        serialized = json.dumps(data, sort_keys=True).encode("utf-8")
        return hashlib.sha256(serialized).hexdigest()[:16]

    @staticmethod
    def _hash_custom_properties(obj) -> str:
        """Hash custom properties dict."""
        custom = {}
        if hasattr(obj, "get"):
            for key in obj.keys():
                val = obj[key]
                if isinstance(val, (int, float)):
                    custom[key] = round(val, 5)
                else:
                    custom[key] = str(val)
        data = json.dumps(custom, sort_keys=True).encode("utf-8")
        return hashlib.sha256(data).hexdigest()[:16]

    @classmethod
    def snapshot_object(cls, obj, include_geometry=True) -> ObjectSnapshot:
        """Create a snapshot of a single object."""
        transform_hash = cls._hash_matrix_world(obj.matrix_world)
        geometry_hash = cls._hash_mesh_data(obj) if include_geometry else None

        # Material hashes
        material_hashes = {}
        if hasattr(obj, "material_slots"):
            for i, slot in enumerate(obj.material_slots):
                if slot.material:
                    material_hashes[str(i)] = cls._hash_material_properties(slot.material)

        light_hash = cls._hash_light_properties(obj)
        camera_hash = cls._hash_camera_properties(obj)
        custom_props_hash = cls._hash_custom_properties(obj)

        return ObjectSnapshot(
            name=obj.name,
            type=obj.type,
            transform_hash=transform_hash,
            geometry_hash=geometry_hash,
            material_hashes=material_hashes,
            light_hash=light_hash,
            camera_hash=camera_hash,
            custom_props_hash=custom_props_hash,
            visible=obj.visible_get(),
        )

    @classmethod
    def snapshot_scene(cls, context, include_geometry=True) -> SceneSnapshot:
        """Create a snapshot of the entire scene."""
        scene = context.scene
        objects = {}

        for obj in scene.objects:
            try:
                snap = cls.snapshot_object(obj, include_geometry=include_geometry)
                objects[obj.name] = snap
            except Exception as e:
                logger.warning(f"Error snapshotting object {obj.name}: {e}")

        # Global hash of all object hashes
        obj_hashes = sorted(
            f"{name}:{snap.transform_hash}:{snap.geometry_hash}:{snap.camera_hash}"
            for name, snap in objects.items()
        )
        global_data = json.dumps(obj_hashes).encode("utf-8")
        global_hash = hashlib.sha256(global_data).hexdigest()[:16]

        return SceneSnapshot(
            scene_name=scene.name,
            objects=objects,
            global_hash=global_hash,
        )


class SceneDiffEngine:
    """Computes deltas between scene snapshots."""

    @staticmethod
    def _extract_transform_delta(name: str, old_snap: ObjectSnapshot, new_snap: ObjectSnapshot,
                                 context) -> Optional[ObjectDelta]:
        """Extract transform changes if matrix changed."""
        if old_snap.transform_hash == new_snap.transform_hash:
            return None

        # Get the actual matrix from the scene object
        scene = context.scene
        obj = scene.objects.get(name)
        if not obj:
            return None

        matrix_floats = [x for col in obj.matrix_world for x in col]

        delta = ObjectDelta(name=name, type=new_snap.type)
        delta.changes["transform"] = matrix_floats
        return delta

    @staticmethod
    def _extract_geometry_delta(name: str, old_snap: ObjectSnapshot, new_snap: ObjectSnapshot,
                                context) -> Optional[ObjectDelta]:
        """Mark geometry as changed (triggers full resync on server)."""
        if old_snap.geometry_hash == new_snap.geometry_hash:
            return None

        delta = ObjectDelta(name=name, type=new_snap.type)
        delta.changes["geometry"] = True
        return delta

    @staticmethod
    def _extract_material_deltas(name: str, old_snap: ObjectSnapshot, new_snap: ObjectSnapshot,
                                 context) -> list[ObjectDelta]:
        """Extract material changes."""
        deltas = []

        # Check for new or changed materials
        for slot_idx, new_hash in new_snap.material_hashes.items():
            old_hash = old_snap.material_hashes.get(slot_idx)
            if old_hash != new_hash:
                delta = ObjectDelta(name=name, type=new_snap.type)
                delta.changes[f"material_{slot_idx}"] = True  # Marker; full resync for now
                deltas.append(delta)

        # Check for removed materials
        for slot_idx in old_snap.material_hashes:
            if slot_idx not in new_snap.material_hashes:
                delta = ObjectDelta(name=name, type=new_snap.type)
                delta.changes[f"material_{slot_idx}_removed"] = True
                deltas.append(delta)

        return deltas

    @staticmethod
    def _extract_light_delta(name: str, old_snap: ObjectSnapshot, new_snap: ObjectSnapshot,
                             context) -> Optional[ObjectDelta]:
        """Extract light property changes."""
        if old_snap.light_hash == new_snap.light_hash:
            return None

        scene = context.scene
        obj = scene.objects.get(name)
        if not obj or not hasattr(obj, "data"):
            return None

        light = obj.data
        delta = ObjectDelta(name=name, type=new_snap.type)
        delta.changes["light"] = {
            "energy": float(light.energy) if hasattr(light, "energy") else None,
            "color": list(light.color) if hasattr(light, "color") else None,
        }
        return delta

    @staticmethod
    def _extract_camera_delta(name: str, old_snap: ObjectSnapshot, new_snap: ObjectSnapshot,
                              context) -> Optional[ObjectDelta]:
        """Extract camera property changes."""
        if old_snap.camera_hash == new_snap.camera_hash:
            return None

        scene = context.scene
        obj = scene.objects.get(name)
        if not obj or not hasattr(obj, "data"):
            return None

        cam = obj.data
        delta = ObjectDelta(name=name, type=new_snap.type)
        delta.changes["camera"] = {
            "lens": float(cam.lens) if hasattr(cam, "lens") else None,
        }
        return delta

    @classmethod
    def compute_delta(cls, old_snapshot: SceneSnapshot, new_snapshot: SceneSnapshot,
                      context, max_delta_bytes=1000000) -> SceneDelta:
        """Compute differences between two snapshots.

        Returns a SceneDelta with all changes. If delta would be > max_delta_bytes,
        sets full_resync_required=True as a hint to fallback to full scene transfer.
        """
        import time

        timestamp = time.time()
        modified_objects = []
        deleted_objects = []
        new_objects = []

        # Detect new and modified objects
        for name, new_snap in new_snapshot.objects.items():
            old_snap = old_snapshot.objects.get(name)
            if old_snap is None:
                new_objects.append(name)
            else:
                # Check for changes
                obj_deltas = []

                # Transform
                t_delta = cls._extract_transform_delta(name, old_snap, new_snap, context)
                if t_delta:
                    obj_deltas.append(t_delta)

                # Geometry
                g_delta = cls._extract_geometry_delta(name, old_snap, new_snap, context)
                if g_delta:
                    obj_deltas.append(g_delta)

                # Materials
                mat_deltas = cls._extract_material_deltas(name, old_snap, new_snap, context)
                obj_deltas.extend(mat_deltas)

                # Light
                l_delta = cls._extract_light_delta(name, old_snap, new_snap, context)
                if l_delta:
                    obj_deltas.append(l_delta)

                # Camera
                c_delta = cls._extract_camera_delta(name, old_snap, new_snap, context)
                if c_delta:
                    obj_deltas.append(c_delta)

                # Merge into single delta per object
                if obj_deltas:
                    merged = ObjectDelta(name=name, type=new_snap.type)
                    for d in obj_deltas:
                        merged.changes.update(d.changes)
                    modified_objects.append(merged)

        # Detect deleted objects
        for name in old_snapshot.objects:
            if name not in new_snapshot.objects:
                deleted_objects.append(name)

        # Create delta
        delta = SceneDelta(
            timestamp=timestamp,
            modified_objects=modified_objects,
            deleted_objects=deleted_objects,
            new_objects=new_objects,
        )

        # Check if delta is too large
        delta_size = delta.size_estimate()
        if delta_size > max_delta_bytes:
            logger.warning(
                f"Delta size {delta_size} bytes exceeds threshold {max_delta_bytes}. "
                f"Recommending full resync."
            )
            delta.full_resync_required = True

        return delta

    @classmethod
    def apply_delta_to_snapshot(cls, snapshot: SceneSnapshot, delta: SceneDelta) -> SceneSnapshot:
        """Apply a delta to a snapshot to get a new snapshot (for local caching).

        This doesn't modify Blender state, just updates the Python representation.
        """
        # This is mostly for the client to predict new state locally
        # In practice, we'll fetch a new snapshot after applying changes in Blender
        new_objects = dict(snapshot.objects)

        for obj_name in delta.deleted_objects:
            new_objects.pop(obj_name, None)

        for new_obj_name in delta.new_objects:
            # Will be filled in by the next snapshot
            pass

        # Recompute global hash
        obj_hashes = sorted(
            f"{name}:{snap.transform_hash}"
            for name, snap in new_objects.items()
        )
        global_data = json.dumps(obj_hashes).encode("utf-8")
        global_hash = hashlib.sha256(global_data).hexdigest()[:16]

        return SceneSnapshot(
            scene_name=snapshot.scene_name,
            objects=new_objects,
            global_hash=global_hash,
        )


# ============================================================================
# Convenience API
# ============================================================================


class SceneStateTracker:
    """High-level API for tracking and diffing Blender scene state.

    Usage:
        tracker = SceneStateTracker()
        tracker.update(context)  # Initial snapshot
        # ... user modifies scene ...
        delta = tracker.compute_delta(context)
        if not delta.full_resync_required:
            send_delta_to_server(delta)
            tracker.update(context)  # Snapshot new state
    """

    def __init__(self):
        self.last_snapshot: Optional[SceneSnapshot] = None
        self.metrics = {
            "deltas_computed": 0,
            "full_resyncs": 0,
            "total_delta_bytes_sent": 0,
            "total_bandwidth_saved": 0,
            "average_delta_size": 0,
        }

    def update(self, context, include_geometry=True):
        """Capture current scene state."""
        self.last_snapshot = SceneDiffer.snapshot_scene(context, include_geometry=include_geometry)
        logger.debug(f"Scene snapshot captured: {len(self.last_snapshot.objects)} objects")

    def compute_delta(self, context, max_delta_bytes=1000000) -> Optional[SceneDelta]:
        """Compute delta since last snapshot.

        Returns None if no previous snapshot exists.
        """
        if self.last_snapshot is None:
            logger.info("No previous snapshot; skipping delta (will send full scene)")
            return None

        current_snapshot = SceneDiffer.snapshot_scene(context, include_geometry=True)
        delta = SceneDiffEngine.compute_delta(
            self.last_snapshot, current_snapshot, context, max_delta_bytes=max_delta_bytes
        )

        # Update metrics
        self.metrics["deltas_computed"] += 1
        delta_size = delta.size_estimate()
        self.metrics["total_delta_bytes_sent"] += delta_size

        if delta.full_resync_required:
            self.metrics["full_resyncs"] += 1
        else:
            # Estimate bandwidth saved (full .blend would be ~100MB, delta is KB)
            estimated_full_size = 100_000_000  # 100MB average
            saved = max(0, estimated_full_size - delta_size)
            delta.bandwidth_saved_bytes = saved
            self.metrics["total_bandwidth_saved"] += saved

        self.metrics["average_delta_size"] = (
            self.metrics["total_delta_bytes_sent"] / max(1, self.metrics["deltas_computed"])
        )

        logger.info(
            f"Delta computed: {delta_size} bytes, "
            f"{len(delta.modified_objects)} modified objects, "
            f"full_resync={delta.full_resync_required}"
        )

        return delta

    def get_metrics(self) -> dict:
        """Return collected performance metrics."""
        return dict(self.metrics)
