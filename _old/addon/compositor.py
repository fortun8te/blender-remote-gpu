"""Live compositor integration — re-evaluates compositor graph as passes arrive.

When render completes, instead of waiting for full EXR:
1. Beauty pass arrives → display in viewport
2. Normal, Depth, Diffuse arrive → compositor can do color correction + depth effects
3. Full passes arrive → material breakdown available for advanced compositing
4. Cryptomatte → keying/selection tools unlock

Each pass arrival triggers compositor re-evaluation for affected node groups.
"""

import logging
from typing import Callable, Optional, Dict, Any
from enum import Enum

logger = logging.getLogger("remote-gpu.addon.compositor")


class PassArrivalEvent:
    """Event fired when a new pass becomes available."""

    def __init__(self, pass_name: str, pass_data: bytes, channels: int,
                 width: int, height: int, data_format: str):
        self.pass_name = pass_name
        self.pass_data = pass_data
        self.channels = channels
        self.width = width
        self.height = height
        self.data_format = data_format
        self.timestamp = None  # Set by event dispatcher


class CompositorNode:
    """Represents a Blender compositor node input that depends on a pass."""

    def __init__(self, node_name: str, socket_index: int, expected_pass: str):
        self.node_name = node_name
        self.socket_index = socket_index
        self.expected_pass = expected_pass
        self.is_connected = False


class LiveCompositor:
    """Manages live compositor updates as passes arrive.

    Maintains a map of compositor nodes that depend on specific passes.
    When a pass arrives, re-evaluates the compositor graph to update
    affected nodes (color correction, effects, keying, etc.).

    Usage:
        compositor = LiveCompositor(bpy.context.scene)
        compositor.on_pass_arrival(pass_event)  # Called by connection.py
    """

    # Map of node types that support live updates
    UPDATABLE_NODES = {
        "CompositorNodeImage": ["Beauty", "Normal", "Depth", "Diffuse Color"],
        "CompositorNodeAlphaOver": ["Image", "Image_001"],
        "CompositorNodeRLayers": ["Image"],  # Render Layers node
        "CompositorNodeFileOutput": ["Image"],
        "CompositorNodeViewer": ["Image"],
        "CompositorNodeIDMask": ["ID"],  # For Cryptomatte
        "CompositorNodeMatte": ["Image"],  # For keying
    }

    def __init__(self, scene=None):
        """Initialize live compositor.

        Args:
            scene: Blender scene object (bpy.context.scene)
        """
        self.scene = scene
        self._pass_cache: Dict[str, Any] = {}  # {pass_name: pass_data}
        self._pass_node_map: Dict[str, list[CompositorNode]] = {}  # {pass_name: [nodes]}
        self._last_eval_time: float = None
        self._eval_count: int = 0

        # Initialize pass-to-node mapping
        if scene:
            self._scan_compositor_nodes()

    def _scan_compositor_nodes(self):
        """Scan compositor node tree and build pass→node dependency map."""
        if not self.scene or not hasattr(self.scene, "node_tree"):
            logger.warning("Scene has no compositor node tree")
            return

        node_tree = self.scene.node_tree
        if not node_tree or not hasattr(node_tree, "nodes"):
            return

        for node in node_tree.nodes:
            node_type = node.bl_idname

            # Skip non-compositor nodes
            if not node_type.startswith("CompositorNode"):
                continue

            # Map nodes that reference specific passes
            if node_type == "CompositorNodeImage":
                # Image node with file path — check if it references a pass name
                if hasattr(node, "image") and node.image:
                    pass_name = node.image.name
                    self._add_node_to_map(pass_name, CompositorNode(node.name, 0, pass_name))

            elif node_type == "CompositorNodeRLayers":
                # Render Layers node — outputs multiple passes
                if hasattr(node, "outputs"):
                    for idx, output in enumerate(node.outputs):
                        pass_name = output.name
                        self._add_node_to_map(pass_name, CompositorNode(node.name, idx, pass_name))

    def _add_node_to_map(self, pass_name: str, comp_node: CompositorNode):
        """Add node to pass dependency map."""
        if pass_name not in self._pass_node_map:
            self._pass_node_map[pass_name] = []
        self._pass_node_map[pass_name].append(comp_node)
        logger.debug(f"Mapped {comp_node.node_name}[{comp_node.socket_index}] → {pass_name}")

    def on_pass_arrival(self, event: PassArrivalEvent):
        """Called when a new pass arrives from the render server.

        Args:
            event: PassArrivalEvent with pass data
        """
        if not self.scene:
            logger.warning("No scene set in LiveCompositor")
            return

        logger.info(f"Pass arrival: {event.pass_name} ({event.width}x{event.height}, {event.channels}ch)")

        # Store pass in cache
        self._pass_cache[event.pass_name] = {
            "data": event.pass_data,
            "channels": event.channels,
            "width": event.width,
            "height": event.height,
            "format": event.data_format,
            "timestamp": event.timestamp,
        }

        # Get affected nodes
        affected_nodes = self._pass_node_map.get(event.pass_name, [])

        if not affected_nodes:
            logger.debug(f"No nodes reference {event.pass_name}")
            return

        logger.info(f"Updating {len(affected_nodes)} nodes for {event.pass_name}")

        # Re-evaluate compositor for affected nodes
        self._update_affected_nodes(event.pass_name, affected_nodes)

    def _update_affected_nodes(self, pass_name: str, affected_nodes: list[CompositorNode]):
        """Update compositor nodes and trigger re-evaluation.

        Args:
            pass_name: Name of the pass that arrived
            affected_nodes: List of compositor nodes that depend on this pass
        """
        if not self.scene or not hasattr(self.scene, "node_tree"):
            return

        try:
            node_tree = self.scene.node_tree

            for comp_node in affected_nodes:
                # Find the actual Blender node object
                blender_node = node_tree.nodes.get(comp_node.node_name)
                if not blender_node:
                    logger.warning(f"Node not found: {comp_node.node_name}")
                    continue

                # Update node inputs with pass data
                self._update_node_input(blender_node, pass_name, comp_node.socket_index)

            # Trigger compositor re-evaluation
            self._evaluate_compositor(pass_name)
            self._eval_count += 1

        except Exception as e:
            logger.error(f"Error updating nodes for {pass_name}: {e}")

    def _update_node_input(self, node, pass_name: str, socket_idx: int = 0):
        """Update a compositor node input with pass data.

        This is a placeholder. Real implementation would:
        1. Create temporary image texture from pass data
        2. Connect it to the node input
        3. Mark node for re-evaluation
        """
        try:
            if hasattr(node, "inputs") and socket_idx < len(node.inputs):
                input_socket = node.inputs[socket_idx]
                logger.debug(f"Updated {node.name}.{input_socket.name} for {pass_name}")
        except Exception as e:
            logger.warning(f"Could not update node input: {e}")

    def _evaluate_compositor(self, pass_name: str):
        """Trigger compositor re-evaluation for affected area.

        Ideally this would only re-evaluate nodes downstream of the updated pass,
        not the entire graph. But for simplicity, can mark entire tree dirty.
        """
        try:
            # In Blender, compositor evaluation happens automatically when
            # node outputs change, or can be forced with:
            # scene.use_nodes = True (or toggle to re-eval)

            if hasattr(self.scene, "node_tree"):
                # Mark tree as needing update (Blender will re-evaluate)
                self.scene.node_tree.update()
                logger.debug(f"Compositor re-evaluated for {pass_name}")
        except Exception as e:
            logger.warning(f"Could not re-evaluate compositor: {e}")

    def get_pass_status(self) -> Dict[str, Any]:
        """Get status of all received passes.

        Returns:
            {
                "passes_received": int,
                "passes": {pass_name: {width, height, channels, timestamp}},
                "eval_count": int,
            }
        """
        passes_info = {
            name: {
                "width": data["width"],
                "height": data["height"],
                "channels": data["channels"],
                "format": data["format"],
            }
            for name, data in self._pass_cache.items()
        }

        return {
            "passes_received": len(self._pass_cache),
            "passes": passes_info,
            "evaluations": self._eval_count,
        }

    def clear_passes(self):
        """Clear cached passes (e.g., before new render)."""
        self._pass_cache.clear()
        self._eval_count = 0
        logger.info("Pass cache cleared")
