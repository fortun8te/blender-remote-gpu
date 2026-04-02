# Addon Bug Fix Checklist

## Critical Path — Fix Before Release

### Phase 1: Crash Prevention (Must Fix)

- [ ] **sync.py:48-56** — Add null checks for context.region and rv3d
  ```python
  def get_view_matrices(context) -> tuple[list, list, tuple]:
      region = context.region
      rv3d = context.region_data
      if region is None or rv3d is None:
          return [0]*16, [0]*16, (1280, 720)
  ```

- [ ] **engine.py:204** — Add null check in _draw_cached_frame()
  ```python
  def _draw_cached_frame(self, context):
      region = context.region
      if region is None:
          return
  ```

- [ ] **engine.py:196** — Validate viewport dimensions before GPU texture
  ```python
  if width <= 0 or height <= 0:
      return
  ```

- [ ] **engine.py:96** — Validate pixel array size before reshape
  ```python
  if len(pixels.flatten()) != pixel_count * 4:
      self.report({"ERROR"}, f"Frame size mismatch")
      return
  ```

- [ ] **engine.py:28** — Add thread lock to _connection class variable
  ```python
  import threading
  class RemoteRenderEngine(bpy.types.RenderEngine):
      _connection_lock = threading.Lock()
      _connection: Connection | None = None
  ```

- [ ] **engine.py:169-191** — Initialize _last_texture in __init__
  ```python
  def __init__(self):
      self._last_texture = None
      self._last_texture_size = (0, 0)
  ```

- [ ] **sync.py:15-31** — Wrap file I/O in try/except
  ```python
  try:
      bpy.ops.wm.save_as_mainfile(filepath=tmp, copy=True, compress=True)
      with open(tmp, "rb") as f:
          data = f.read()
  except (RuntimeError, OSError, PermissionError) as e:
      raise RuntimeError(f"Failed to save .blend: {e}")
  finally:
      try:
          os.unlink(tmp)
      except OSError:
          pass
  ```

- [ ] **preferences.py:67** — Safe addon preferences access
  ```python
  try:
      prefs = context.preferences.addons[__package__].preferences
  except (KeyError, AttributeError):
      layout.label(text="Addon not properly loaded")
      return
  ```

- [ ] **preferences.py:8** — Use explicit bl_idname instead of __package__
  ```python
  class RemoteGPUPreferences(bpy.types.AddonPreferences):
      bl_idname = "blender_remote_gpu"  # Not __package__
  ```

---

### Phase 2: Concurrency & Connectivity (High Priority)

- [ ] **connection.py:68-88** — Prevent double-connect threads
  ```python
  def connect(self):
      if self._thread is not None and self._thread.is_alive():
          self._stop_event.set()
          self._thread.join(timeout=1.0)
          self._thread = None
      
      self._stop_event.clear()
      self._thread = threading.Thread(target=self._run_loop, daemon=True)
      self._thread.start()
  ```

- [ ] **engine.py:28** — Use lock when accessing _connection
  ```python
  # In every method that reads/writes _connection:
  with RemoteRenderEngine._connection_lock:
      conn = RemoteRenderEngine._connection
      if conn is None:
          return
  ```

- [ ] **connection.py:175-182** — Handle asyncio loop properly
  ```python
  def _run_loop(self):
      try:
          loop = asyncio.new_event_loop()
          asyncio.set_event_loop(loop)
          loop.run_until_complete(self._async_loop())
      except RuntimeError as e:
          self._event_queue.put({"error": f"Event loop error: {e}"})
      except Exception as e:
          self._event_queue.put({"error": str(e)})
      finally:
          loop.close()
          self.connected = False
  ```

---

### Phase 3: Error Recovery & Robustness

- [ ] **engine.py:162-166** — Implement exponential backoff for frame decode errors
  ```python
  def view_draw(self, context, depsgraph):
      # ... 
      frame = conn.get_frame()
      if frame is not None:
          jpeg_data, meta = frame
          try:
              pixels, width, height = decode_jpeg(jpeg_data)
              self._frame_decode_errors = 0  # Reset counter
              self._draw_frame(context, pixels, width, height)
          except Exception as e:
              self._frame_decode_errors = getattr(self, "_frame_decode_errors", 0) + 1
              if self._frame_decode_errors > 10:
                  self._draw_placeholder(context, "Frame decode failed — reconnect?")
              else:
                  self._draw_placeholder(context, f"Frame error (retry {self._frame_decode_errors}/10)")
  ```

- [ ] **engine.py:175** — Only redraw when connected
  ```python
  if conn is not None and conn.connected:
      self.tag_redraw()
  ```

- [ ] **frame.py:20-22** — Add JPEG corruption handling
  ```python
  def decode_jpeg(data: bytes) -> tuple[np.ndarray, int, int]:
      if Image is None:
          raise RuntimeError("Pillow not installed")
      
      try:
          img = Image.open(io.BytesIO(data))
          img.verify()  # Pre-check
          img = Image.open(io.BytesIO(data))  # Re-open after verify
          img = img.convert("RGBA")
      except (IOError, SyntaxError) as e:
          raise RuntimeError(f"Corrupted JPEG frame: {e}")
      
      # ... rest of function
  ```

- [ ] **engine.py:59** — Use depsgraph instead of bpy.context
  ```python
  def render(self, depsgraph):
      scene = depsgraph.scene
      render = scene.render
      
      # Use scene directly instead of bpy.context
      settings = {
          "resolution_x": render.resolution_x,
          "resolution_y": render.resolution_y,
          "samples": scene.cycles.samples if hasattr(scene, "cycles") else 128,
      }
  ```

---

### Phase 4: Validation & Cleanup

- [ ] **engine.py:91-99** — Use try/finally for render result cleanup
  ```python
  result = self.begin_result(0, 0, res_x, res_y)
  try:
      if not result.layers or "Combined" not in result.layers[0].passes:
          self.report({"ERROR"}, "Internal render setup failed")
          return
      
      layer = result.layers[0].passes["Combined"]
      pixels_reshaped = pixels.reshape(pixel_count, 4).tolist()
      layer.rect = pixels_reshaped
  finally:
      self.end_result(result)
  ```

- [ ] **preferences.py:140** — Close old connection before new
  ```python
  def execute(self, context):
      old_conn = engine.RemoteRenderEngine._connection
      if old_conn is not None:
          old_conn.close()
      
      conn = Connection(url)
      conn.connect()
      engine.RemoteRenderEngine._connection = conn
  ```

- [ ] **engine.py:13** — Add fallback for GPU API changes
  ```python
  try:
      from gpu_extras.presets import draw_texture_2d
  except ImportError:
      # Blender 5.2+ fallback
      def draw_texture_2d(texture, pos, width, height):
          # TODO: Implement using gpu.shader directly
          pass
  ```

---

## Optional Enhancements (Polish)

- [ ] Add logging instead of print()
- [ ] Implement render timeout (300 sec max)
- [ ] Add SCENE_ACK validation
- [ ] Fix EXR decoder (use OpenEXR library)
- [ ] Implement proper format consistency (PNG vs JPEG vs EXR)
- [ ] Add server IP validation (socket.gethostbyname)
- [ ] Log when frames are dropped from queue
- [ ] Add PONG response tracking

---

## Testing Checklist

After fixes, test these scenarios:

- [ ] F12 viewport mode toggle
- [ ] Minimize and restore viewport
- [ ] Collapse/expand sidebar during render
- [ ] Connect, timeout, reconnect immediately
- [ ] Network disconnect during frame delivery
- [ ] Server returns wrong resolution
- [ ] CLI render (`blender -b scene.blend -f 1`)
- [ ] Temp directory full
- [ ] Addon disabled, open Render Properties
- [ ] Server returns vram_total=0
- [ ] Multiple concurrent renders (viewport + F12)

---

## Delivery

- [ ] All Priority 1 fixes complete
- [ ] Unit tests pass
- [ ] Manual testing of crash scenarios complete
- [ ] Code review
- [ ] Document in CHANGELOG
- [ ] Tag as v0.1.1-beta

