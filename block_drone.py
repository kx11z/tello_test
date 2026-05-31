"""
Block Drone — a Scratch-style block-coding GUI that controls a REAL DJI Tello.

Drag command blocks from the palette on the left into the script area on the
right. Blocks snap together into a vertical stack (and into the body of a
"repeat" block). Click a number/word inside a block to change it. Press Run to
fly the actual drone through the sequence.

Requires: djitellopy  (pip install djitellopy)
Standard library only otherwise (tkinter).
"""

import json
import math
import queue
import threading
import time
import tkinter as tk
import tkinter.font as tkfont
from tkinter import filedialog, messagebox, simpledialog

# The drone library is optional at import time so you can still build/test
# scripts (in "Dry run" mode) on a machine without a drone attached.
try:
    from djitellopy import Tello
    TELLO_AVAILABLE = True
except Exception:  # pragma: no cover - only when lib missing
    Tello = None
    TELLO_AVAILABLE = False


# --------------------------------------------------------------------------- #
# Block definitions
# --------------------------------------------------------------------------- #
# Each parameter spec is a tuple:
#   ('int',   default, min, max)
#   ('float', default, min, max)
#   ('choice', [options...])           -> default is options[0]
#
# Each block "segment" is either ('text', "literal") or ('param', "name").

CAT_COLORS = {
    "motion": "#4C97FF",    # blue
    "control": "#FF8C1A",   # orange
    "settings": "#59C059",  # green
    "event": "#9966FF",     # purple (the hat)
}

SPECS = {
    "hat": dict(cat="event", container=True,
                segments=[("text", "when ▶ Run clicked")], params={}),

    "takeoff": dict(cat="motion", segments=[("text", "take off")], params={}),
    "land":    dict(cat="motion", segments=[("text", "land")], params={}),

    "up":    dict(cat="motion", params={"cm": ("int", 50, 20, 500)},
                  segments=[("text", "move up"), ("param", "cm"), ("text", "cm")]),
    "down":  dict(cat="motion", params={"cm": ("int", 50, 20, 500)},
                  segments=[("text", "move down"), ("param", "cm"), ("text", "cm")]),
    "forward": dict(cat="motion", params={"cm": ("int", 50, 20, 500)},
                    segments=[("text", "move forward"), ("param", "cm"), ("text", "cm")]),
    "back":  dict(cat="motion", params={"cm": ("int", 50, 20, 500)},
                  segments=[("text", "move back"), ("param", "cm"), ("text", "cm")]),
    "left":  dict(cat="motion", params={"cm": ("int", 50, 20, 500)},
                  segments=[("text", "move left"), ("param", "cm"), ("text", "cm")]),
    "right": dict(cat="motion", params={"cm": ("int", 50, 20, 500)},
                  segments=[("text", "move right"), ("param", "cm"), ("text", "cm")]),

    "cw":  dict(cat="motion", params={"deg": ("int", 90, 1, 360)},
                segments=[("text", "turn right ↻"), ("param", "deg"), ("text", "°")]),
    "ccw": dict(cat="motion", params={"deg": ("int", 90, 1, 360)},
                segments=[("text", "turn left ↺"), ("param", "deg"), ("text", "°")]),

    "flip": dict(cat="motion",
                 params={"dir": ("choice", ["forward", "back", "left", "right"])},
                 segments=[("text", "flip"), ("param", "dir")]),

    "speed": dict(cat="settings", params={"cmps": ("int", 50, 10, 100)},
                  segments=[("text", "set speed"), ("param", "cmps"), ("text", "cm/s")]),

    "wait":   dict(cat="control", params={"sec": ("float", 1.0, 0.0, 60.0)},
                   segments=[("text", "wait"), ("param", "sec"), ("text", "seconds")]),
    "repeat": dict(cat="control", container=True, params={"n": ("int", 4, 1, 100)},
                   segments=[("text", "repeat"), ("param", "n"), ("text", "times")]),
}

# Palette layout (groups -> kinds). The hat is not in the palette.
GROUPS = [
    ("Motion", ["takeoff", "land", "up", "down", "forward", "back",
                "left", "right", "cw", "ccw", "flip"]),
    ("Control", ["wait", "repeat"]),
    ("Settings", ["speed"]),
]

# Geometry
BLOCK_H = 34
INDENT = 22
EMPTY_BODY_H = 22
FOOT_H = 14
BASE_X = 24
BASE_Y = 18
MIN_W = 64
CORNER = 8
DRAG_THRESHOLD = 5
PALETTE_W = 210

# Simulation
TAKEOFF_H = 100      # cm the drone rises to on take off (Tello is ~80-100cm)
DEFAULT_SPEED = 50   # cm/s used to time movement segments when no "set speed"
TURN_SPEED = 90.0    # deg/s used to time rotations

# How often the main thread drains UI updates queued by worker threads (ms).
UI_POLL_MS = 16


def param_default(p):
    return p[1][0] if p[0] == "choice" else p[1]


def shade(hexc, f=0.8):
    """Darken (f<1) or lighten (f>1) a hex colour, clamped to 0-255."""
    hexc = hexc.lstrip("#")
    r, g, b = (max(0, min(255, int(int(hexc[i:i + 2], 16) * f)))
               for i in (0, 2, 4))
    return f"#{r:02x}{g:02x}{b:02x}"


def round_rect_points(x1, y1, x2, y2, r):
    """Point list for a rounded rectangle (use with create_polygon smooth=True)."""
    return [
        x1 + r, y1, x2 - r, y1, x2, y1,
        x2, y1 + r, x2, y2 - r, x2, y2,
        x2 - r, y2, x1 + r, y2, x1, y2,
        x1, y2 - r, x1, y1 + r, x1, y1,
    ]


# --------------------------------------------------------------------------- #
# Block model
# --------------------------------------------------------------------------- #
class Block:
    def __init__(self, kind):
        self.kind = kind
        spec = SPECS[kind]
        self.params = {name: param_default(p) for name, p in spec["params"].items()}
        self.children = [] if spec.get("container") else None
        # Filled in during layout:
        self.bbox = None
        self.param_bboxes = {}

    def is_container(self):
        return self.children is not None

    def to_dict(self):
        d = {"kind": self.kind, "params": self.params}
        if self.is_container():
            d["children"] = [c.to_dict() for c in self.children]
        return d

    @staticmethod
    def from_dict(d):
        b = Block(d["kind"])
        specs = SPECS[b.kind]["params"]
        for name, val in d.get("params", {}).items():
            if name not in specs:
                continue
            p = specs[name]
            if p[0] in ("int", "float"):
                cast = int if p[0] == "int" else float
                val = max(p[2], min(p[3], cast(val)))
            elif p[0] == "choice" and val not in p[1]:
                val = p[1][0]
            b.params[name] = val
        if b.is_container():
            b.children = [Block.from_dict(c) for c in d.get("children", [])]
        return b


# --------------------------------------------------------------------------- #
# A flat, colour-controllable button.
# (tk.Button ignores bg/fg under the macOS Aqua theme, which kills contrast,
#  so we build our buttons out of tk.Label instead.)
# --------------------------------------------------------------------------- #
class FlatButton(tk.Label):
    def __init__(self, master, text, command, bg, font=None):
        super().__init__(master, text=text, bg=bg, fg="white", font=font,
                         padx=12, pady=5, cursor="hand2")
        self._command = command
        self._bg = bg
        self._enabled = True
        self.bind("<Button-1>", self._on_click)
        self.bind("<Enter>", self._on_enter)
        self.bind("<Leave>", self._on_leave)

    def _on_click(self, _e):
        if self._enabled and self._command:
            self._command()

    def _on_enter(self, _e):
        if self._enabled:
            self.config(bg=shade(self._bg, 1.25))

    def _on_leave(self, _e):
        self.config(bg=self._bg if self._enabled else shade(self._bg, 0.5))

    def set_enabled(self, on):
        self._enabled = bool(on)
        self.config(bg=self._bg if on else shade(self._bg, 0.5),
                    fg="white" if on else "#9a9aa8",
                    cursor="hand2" if on else "arrow")


# --------------------------------------------------------------------------- #
# Application
# --------------------------------------------------------------------------- #
class App:
    def __init__(self, root):
        self.root = root
        root.title("Block Drone — Tello block coding")
        root.geometry("1340x760")
        root.minsize(1080, 600)

        self.font = tkfont.Font(family="Helvetica", size=11, weight="bold")
        self.small = tkfont.Font(family="Helvetica", size=10)

        # Program: a fixed hat block whose children are the top-level script.
        self.hat = Block("hat")

        # Drone / run state
        self.tello = None
        self.running = False
        self.run_is_dry = False
        self.run_seq = 0           # bumped per run; guards stale finish callbacks
        self.stop_flag = threading.Event()
        self.drone_lock = threading.Lock()
        self.highlight_block = None
        self._closed = False
        self._batt_after = None
        self._sync_after = None
        self._buttons_running = False  # last state applied to the Run/Stop buttons

        # tkinter is not thread-safe (and on macOS a widget call from another
        # thread is silently dropped), so worker threads never touch the UI
        # directly — they queue callables here and the main thread runs them.
        self._ui_queue = queue.Queue()
        self._ui_after = None

        # Drag state
        self.press = None          # dict describing the press, or None
        self.moved = False
        self.dragging = None       # Block being dragged, or None
        self.drag_is_new = False
        self.grab_dx = self.grab_dy = 0
        self.drop_slot = None

        # Layout bookkeeping (recomputed every relayout)
        self.slots = []
        self.rendered = []

        self._build_ui()
        self._draw_palette()
        self.relayout()
        self._drain_ui()
        self._battery_loop()
        self._run_state_sync()
        root.protocol("WM_DELETE_WINDOW", self.on_close)

    # ----------------------------- UI scaffold ---------------------------- #
    def _build_ui(self):
        bar = tk.Frame(self.root, bg="#2b2b3a", pady=6, padx=8)
        bar.pack(side="top", fill="x")

        def tb(text, cmd, bg="#3a3a4d"):
            return FlatButton(bar, text, cmd, bg, font=self.small)

        tb("Connect", self.on_connect, bg="#3573cc").pack(side="left", padx=3)
        self.lbl_status = tk.Label(bar, text="● not connected", fg="#cc6666",
                                   bg="#2b2b3a", font=self.small)
        self.lbl_status.pack(side="left", padx=8)
        self.lbl_batt = tk.Label(bar, text="battery: --", fg="#cccccc",
                                 bg="#2b2b3a", font=self.small)
        self.lbl_batt.pack(side="left", padx=4)

        self.btn_run = tb("▶ Run", self.on_run, bg="#3aa34a")
        self.btn_run.pack(side="left", padx=(20, 3))
        self.btn_stop = tb("■ Stop", self.on_stop, bg="#a3852b")
        self.btn_stop.pack(side="left", padx=3)
        self.btn_stop.set_enabled(False)
        tb("Land", self.on_land, bg="#3a3a4d").pack(side="left", padx=3)
        tb("EMERGENCY", self.on_emergency, bg="#c0392b").pack(side="left", padx=3)

        self.dry_run = tk.BooleanVar(value=not TELLO_AVAILABLE)
        tk.Checkbutton(bar, text="Dry run (no drone)", variable=self.dry_run,
                       bg="#2b2b3a", fg="#cccccc", selectcolor="#2b2b3a",
                       font=self.small,
                       activebackground="#2b2b3a", activeforeground="white",
                       highlightthickness=0, bd=0).pack(side="left", padx=10)

        tb("Save", self.on_save).pack(side="right", padx=3)
        tb("Load", self.on_load).pack(side="right", padx=3)
        tb("Clear", self.on_clear).pack(side="right", padx=3)

        # Body: palette | workspace
        body = tk.Frame(self.root)
        body.pack(side="top", fill="both", expand=True)

        pframe = tk.Frame(body, width=PALETTE_W)
        pframe.pack(side="left", fill="y")
        pframe.pack_propagate(False)
        tk.Label(pframe, text="Blocks", bg="#e8e8ef", anchor="w",
                 font=self.font, padx=10, pady=4).pack(side="top", fill="x")
        self.palette = tk.Canvas(pframe, bg="#f0f0f6", highlightthickness=0,
                                 width=PALETTE_W)
        psb = tk.Scrollbar(pframe, orient="vertical", command=self.palette.yview)
        self.palette.configure(yscrollcommand=psb.set)
        psb.pack(side="right", fill="y")
        self.palette.pack(side="left", fill="both", expand=True)

        # 3D simulation panel, docked on the right
        sframe = tk.Frame(body, width=410, bg="#0d0d18")
        sframe.pack(side="right", fill="y")
        sframe.pack_propagate(False)
        self.sim = SimPanel(sframe, self)
        self.sim.pack(fill="both", expand=True)

        wframe = tk.Frame(body)
        wframe.pack(side="left", fill="both", expand=True)
        self.canvas = tk.Canvas(wframe, bg="#f7f7fb", highlightthickness=0)
        wsb = tk.Scrollbar(wframe, orient="vertical", command=self.canvas.yview)
        self.canvas.configure(yscrollcommand=wsb.set)
        wsb.pack(side="right", fill="y")
        self.canvas.pack(side="left", fill="both", expand=True)

        # Log / status strip
        self.log_var = tk.StringVar(
            value="Drag blocks from the left. Click a number to edit it. "
                  "Drop a block back on the palette to delete it.")
        tk.Label(self.root, textvariable=self.log_var, anchor="w", bg="#1e1e28",
                 fg="#b8d8b8", font=self.small, padx=10, pady=5).pack(
                     side="bottom", fill="x")

        # Bindings (drag works across both canvases via root coordinates)
        self.canvas.bind("<ButtonPress-1>", self.on_ws_press)
        self.canvas.bind("<B1-Motion>", self.on_drag)
        self.canvas.bind("<ButtonRelease-1>", self.on_release)
        self.palette.bind("<ButtonPress-1>", self.on_palette_press)
        self.palette.bind("<B1-Motion>", self.on_drag)
        self.palette.bind("<ButtonRelease-1>", self.on_release)
        for c in (self.canvas, self.palette):
            c.bind("<MouseWheel>", lambda e, cv=c: cv.yview_scroll(
                -1 if e.delta > 0 else 1, "units"))
            c.bind("<Button-4>", lambda e, cv=c: cv.yview_scroll(-1, "units"))
            c.bind("<Button-5>", lambda e, cv=c: cv.yview_scroll(1, "units"))

    # ----------------------------- Drawing -------------------------------- #
    def block_layout(self, block):
        """Return (width, layout) where layout is a list of segment placements."""
        spec = SPECS[block.kind]
        pad, gap = 11, 6
        x = pad
        layout = []
        for seg in spec["segments"]:
            if seg[0] == "text":
                w = self.font.measure(seg[1])
                layout.append(("text", seg[1], x, w))
                x += w + gap
            else:
                name = seg[1]
                val = str(block.params[name])
                pw = max(self.font.measure(val) + 14, 26)
                layout.append(("param", name, x, pw))
                x += pw + gap
        width = max(x - gap + pad, MIN_W)
        return width, layout

    def draw_block_header(self, canvas, block, x, y, tag, highlight=False):
        spec = SPECS[block.kind]
        color = CAT_COLORS[spec["cat"]]
        outline = "#FFD500" if highlight else shade(color)
        width = 3 if highlight else 1
        w, layout = self.block_layout(block)
        canvas.create_polygon(round_rect_points(x, y, x + w, y + BLOCK_H, CORNER),
                              smooth=True, fill=color, outline=outline,
                              width=width, tags=tag)
        block.bbox = (x, y, x + w, y + BLOCK_H)
        block.param_bboxes = {}
        cy = y + BLOCK_H / 2
        for item in layout:
            if item[0] == "text":
                canvas.create_text(x + item[2], cy, text=item[1], fill="white",
                                   anchor="w", font=self.font, tags=tag)
            else:
                name, px, pw = item[1], x + item[2], item[3]
                canvas.create_polygon(
                    round_rect_points(px, y + 6, px + pw, y + BLOCK_H - 6, 7),
                    smooth=True, fill="white", outline="", tags=tag)
                canvas.create_text(px + pw / 2, cy, text=str(block.params[name]),
                                   fill="#333333", font=self.font, tags=tag)
                block.param_bboxes[name] = (px, y + 6, px + pw, y + BLOCK_H - 6)
        return w

    def render_block(self, block, x, y, parent_list, index, tag="block", collect=True):
        """Recursively draw a block (and its body). Returns y for the next block."""
        # Slot in the gap directly above this block.
        if collect and parent_list is not None:
            self.slots.append({"parent": parent_list, "index": index, "x": x, "y": y})
        if collect:
            self.rendered.append(block)

        highlight = (block is self.highlight_block)
        w = self.draw_block_header(self.canvas, block, x, y, tag, highlight)
        ny = y + BLOCK_H

        if block.is_container():
            is_hat = block.kind == "hat"
            body_x = x if is_hat else x + INDENT
            cy = ny
            for i, child in enumerate(block.children):
                cy = self.render_block(child, body_x, cy, block.children, i,
                                       tag, collect)
            if collect:
                self.slots.append({"parent": block.children,
                                   "index": len(block.children),
                                   "x": body_x, "y": cy})
            if is_hat:
                ny = cy
            else:
                body_bottom = max(cy, ny + EMPTY_BODY_H)
                color = CAT_COLORS[SPECS[block.kind]["cat"]]
                # Left arm of the C.
                self.canvas.create_rectangle(x, ny - 1, x + INDENT, body_bottom,
                                             fill=color, outline="", tags=tag)
                # Bottom bar of the C.
                self.canvas.create_polygon(
                    round_rect_points(x, body_bottom, x + max(w, INDENT + 40),
                                      body_bottom + FOOT_H, CORNER),
                    smooth=True, fill=color, outline=shade(color), tags=tag)
                ny = body_bottom + FOOT_H
        return ny

    def relayout(self):
        self.canvas.delete("all")
        self.slots = []
        self.rendered = []
        bottom = self.render_block(self.hat, BASE_X, BASE_Y, None, 0)
        self.canvas.configure(scrollregion=(0, 0, 1200, bottom + 120))

    def _draw_palette(self):
        self.palette_hits = []
        y = 10
        for gname, kinds in GROUPS:
            self.palette.create_text(12, y, text=gname.upper(), anchor="w",
                                     fill="#777", font=self.small)
            y += 20
            for kind in kinds:
                pb = Block(kind)
                w = self.draw_block_header(self.palette, pb, 12, y, "pal")
                self.palette_hits.append((12, y, 12 + w, y + BLOCK_H, kind))
                y += BLOCK_H + 8
            y += 10
        self.palette.configure(scrollregion=(0, 0, PALETTE_W, y + 20))

    # ----------------------------- Hit testing ---------------------------- #
    def _to_canvas(self, x_root, y_root):
        cx = x_root - self.canvas.winfo_rootx()
        cy = y_root - self.canvas.winfo_rooty()
        return self.canvas.canvasx(cx), self.canvas.canvasy(cy)

    @staticmethod
    def _inside(bb, x, y):
        return bb and bb[0] <= x <= bb[2] and bb[1] <= y <= bb[3]

    def hittest_ws(self, x, y):
        for b in reversed(self.rendered):
            for name, bb in b.param_bboxes.items():
                if self._inside(bb, x, y):
                    return ("param", b, name)
            if self._inside(b.bbox, x, y):
                if b.kind == "hat":
                    return None
                return ("block", b)
        return None

    def hittest_palette(self, x, y):
        for (x1, y1, x2, y2, kind) in self.palette_hits:
            if x1 <= x <= x2 and y1 <= y <= y2:
                return kind
        return None

    def remove_block(self, target):
        """Detach `target` (and its subtree) from the tree. Returns True if found."""
        def rec(lst):
            for i, b in enumerate(lst):
                if b is target:
                    del lst[i]
                    return True
                if b.is_container() and rec(b.children):
                    return True
            return False
        return rec(self.hat.children)

    # ------------------------------- Drag --------------------------------- #
    def on_ws_press(self, event):
        cx, cy = self._to_canvas(event.x_root, event.y_root)
        self.press = {"where": "ws", "hit": self.hittest_ws(cx, cy),
                      "x_root": event.x_root, "y_root": event.y_root,
                      "cx": cx, "cy": cy}
        self.moved = False
        self.dragging = None

    def on_palette_press(self, event):
        x = self.palette.canvasx(event.x)
        y = self.palette.canvasy(event.y)
        kind = self.hittest_palette(x, y)
        self.press = {"where": "palette", "kind": kind,
                      "x_root": event.x_root, "y_root": event.y_root}
        self.moved = False
        self.dragging = None

    def on_drag(self, event):
        if not self.press:
            return
        if not self.moved:
            dx = event.x_root - self.press["x_root"]
            dy = event.y_root - self.press["y_root"]
            if (dx * dx + dy * dy) ** 0.5 < DRAG_THRESHOLD:
                return
            if not self._begin_drag():
                self.press = None
                return
            self.moved = True

        cx, cy = self._to_canvas(event.x_root, event.y_root)
        fx, fy = cx - self.grab_dx, cy - self.grab_dy
        self._draw_floating(fx, fy)
        self.drop_slot = self._nearest_slot(fx, fy)
        self._draw_drop_indicator(self.drop_slot)

    def _begin_drag(self):
        p = self.press
        if p["where"] == "palette":
            if not p["kind"]:
                return False
            self.dragging = Block(p["kind"])
            self.drag_is_new = True
            self.grab_dx, self.grab_dy = 18, 16
        else:
            hit = p["hit"]
            if not hit:
                return False
            block = hit[1]
            if block.kind == "hat":
                return False
            self.dragging = block
            self.drag_is_new = False
            self.grab_dx = p["cx"] - block.bbox[0]
            self.grab_dy = p["cy"] - block.bbox[1]
            self.remove_block(block)
        self.relayout()  # redraw base tree without the dragged block, refresh slots
        return True

    def _draw_floating(self, fx, fy):
        self.canvas.delete("drag")
        self.render_block(self.dragging, fx, fy, None, 0, tag="drag", collect=False)
        self.canvas.tag_raise("drag")

    def _nearest_slot(self, fx, fy):
        best, bestd = None, None
        for s in self.slots:
            d = (s["x"] - fx) ** 2 + (s["y"] - fy) ** 2
            if bestd is None or d < bestd:
                best, bestd = s, d
        return best

    def _draw_drop_indicator(self, slot):
        self.canvas.delete("dropind")
        if not slot:
            return
        x, y = slot["x"], slot["y"]
        self.canvas.create_rectangle(x, y - 3, x + 150, y + 3, fill="#FFD500",
                                     outline="", tags="dropind")
        self.canvas.tag_raise("drag")

    def on_release(self, event):
        if self.dragging:
            over_palette = self._over_palette(event.x_root, event.y_root)
            if over_palette:
                pass  # dropped on palette -> delete (already detached / discard new)
            elif self.drop_slot:
                self.drop_slot["parent"].insert(self.drop_slot["index"], self.dragging)
            else:
                self.hat.children.append(self.dragging)
            self.dragging = None
            self.drag_is_new = False
            self.drop_slot = None
            self.relayout()
        elif self.press and not self.moved and self.press["where"] == "ws":
            hit = self.press["hit"]
            if hit and hit[0] == "param":
                self.edit_param(hit[1], hit[2])
        self.press = None
        self.moved = False
        self.canvas.delete("drag")
        self.canvas.delete("dropind")

    def _over_palette(self, x_root, y_root):
        px = self.palette.winfo_rootx()
        return px <= x_root <= px + self.palette.winfo_width()

    def edit_param(self, block, name):
        p = SPECS[block.kind]["params"][name]
        if p[0] == "choice":
            opts = p[1]
            i = (opts.index(block.params[name]) + 1) % len(opts)
            block.params[name] = opts[i]
        elif p[0] == "int":
            v = simpledialog.askinteger("Edit value", name, parent=self.root,
                                        initialvalue=block.params[name],
                                        minvalue=p[2], maxvalue=p[3])
            if v is not None:
                block.params[name] = v
        elif p[0] == "float":
            v = simpledialog.askfloat("Edit value", name, parent=self.root,
                                      initialvalue=block.params[name],
                                      minvalue=p[2], maxvalue=p[3])
            if v is not None:
                block.params[name] = v
        self.relayout()

    # ----------------------------- Execution ------------------------------ #
    def flatten(self, lst, out):
        for b in lst:
            if b.kind == "repeat":
                for _ in range(int(b.params["n"])):
                    self.flatten(b.children, out)
            elif b.is_container():
                self.flatten(b.children, out)
            else:
                out.append(b)

    @staticmethod
    def describe(block):
        parts = []
        for seg in SPECS[block.kind]["segments"]:
            parts.append(seg[1] if seg[0] == "text" else str(block.params[seg[1]]))
        return " ".join(parts)

    def on_run(self):
        if self.running:
            return
        dry = self.dry_run.get()
        if not dry and not self.tello:
            messagebox.showwarning("Not connected",
                                   "Connect to the drone first, or tick 'Dry run'.")
            return
        instrs = []
        self.flatten(self.hat.children, instrs)
        if not instrs:
            self.log("Nothing to run — add some blocks first.")
            return
        self.running = True
        self.run_is_dry = dry
        self.run_seq += 1
        seq = self.run_seq
        self.stop_flag.clear()
        self._set_running(True)
        if dry:
            # Dry run: the 3D sim IS the run. Its clock drives the playback,
            # block highlighting and timing — there is no separate worker.
            self.log("(dry) running…")
            self.sim.play_run(build_segments(instrs))
            return
        try:
            threading.Thread(target=self._run_worker,
                             args=(instrs, seq),
                             daemon=True).start()
        except Exception as e:
            # If the worker never starts there is no finally to reset state,
            # so undo it here rather than leaving the buttons stuck.
            self._end_run("Could not start run: " + str(e))

    def _run_worker(self, instrs, seq):
        """Real-drone execution, on a background thread. (Dry runs are driven
        by the 3D sim instead — see on_run / SimPanel.play_run.)"""
        try:
            for b in instrs:
                if self.stop_flag.is_set():
                    self._ui(self.log, "Stopped.")
                    break
                self._ui(self._set_highlight, b)
                self._ui(self.log, "→ " + self.describe(b))
                with self.drone_lock:
                    self.exec_block(b)
                if self.stop_flag.is_set():
                    self._ui(self.log, "Stopped.")
                    break
            else:
                self._ui(self.log, "✔ Finished.")
        except Exception as e:
            self._ui(self.log, "✖ Error: " + str(e) + " — landing for safety.")
            if self.tello:
                self._safe(self.tello.land)
        finally:
            # Reset run state on the main thread, guarded by the run sequence so
            # a finishing run can never clobber a newer one that already started.
            self._ui(self._finish_run, seq)

    def _finish_run(self, seq):
        """Run on the main thread when the drone worker ends. Ignored if a
        newer run has already taken over (its own worker will reset state).
        The worker has already logged the outcome, so this just resets state."""
        if seq != self.run_seq:
            return
        self._end_run()

    def _end_run(self, msg=None):
        """Single place that returns the app to the idle state: clears the
        running flags, re-enables Run / disables Stop, and drops the block
        highlight. Used by every run ending (drone, dry, stop, reset, error)."""
        self.running = False
        self.run_is_dry = False
        self._set_running(False)
        self._set_highlight(None)
        if msg:
            self.log(msg)

    def _dry_finished(self):
        """Called by the sim (main thread) when dry-run playback reaches the
        end. Ignored unless a dry run is actually in progress."""
        if self.running and self.run_is_dry:
            self._end_run("✔ Finished.")

    def exec_block(self, b):
        t, P, k = self.tello, b.params, b.kind
        if k == "takeoff":
            t.takeoff()
        elif k == "land":
            t.land()
        elif k == "up":
            t.move_up(P["cm"])
        elif k == "down":
            t.move_down(P["cm"])
        elif k == "forward":
            t.move_forward(P["cm"])
        elif k == "back":
            t.move_back(P["cm"])
        elif k == "left":
            t.move_left(P["cm"])
        elif k == "right":
            t.move_right(P["cm"])
        elif k == "cw":
            t.rotate_clockwise(P["deg"])
        elif k == "ccw":
            t.rotate_counter_clockwise(P["deg"])
        elif k == "flip":
            t.flip({"forward": "f", "back": "b", "left": "l", "right": "r"}[P["dir"]])
        elif k == "speed":
            t.set_speed(P["cmps"])
        elif k == "wait":
            self._sleep_check(P["sec"])

    def _sleep_check(self, sec):
        end = sec
        step = 0.05
        elapsed = 0.0
        while elapsed < end:
            if self.stop_flag.is_set():
                return False
            time.sleep(step)
            elapsed += step
        return not self.stop_flag.is_set()

    def on_stop(self):
        if not self.running:
            self.log("Nothing is running.")
            return
        if self.run_is_dry:
            # Dry run halts instantly (it's just the sim clock).
            self.sim.stop()
            self._end_run("Stopped.")
        else:
            # A real drone command is mid-flight; ask it to stop after it
            # returns. The worker then resets state via _finish_run.
            self.stop_flag.set()
            self.log("Stopping after current drone command returns...")

    def on_sim_reset(self):
        """Reset button on the 3D panel: rewind the view to the start. If a
        dry run is in progress, this also ends it (like Stop, but rewound)."""
        if self.running and self.run_is_dry:
            self._end_run("Reset.")
        else:
            self._set_highlight(None)
            self.log("View reset.")

    def on_land(self):
        if not self.tello:
            self.log("Not connected.")
            return
        self.stop_flag.set()  # abort any running sequence first

        def _land():
            with self.drone_lock:  # wait for the in-flight command, then land
                self._safe(self.tello.land)
        threading.Thread(target=_land, daemon=True).start()
        self.log("Landing.")

    def on_emergency(self):
        self.stop_flag.set()
        if self.tello:
            threading.Thread(target=lambda: self._safe(self.tello.emergency),
                             daemon=True).start()
        self.log("!!! EMERGENCY — motors cut")

    def _safe(self, fn):
        try:
            fn()
        except Exception as e:
            self._ui(self.log, "Error: " + str(e))

    def _set_running(self, run):
        self._buttons_running = run
        self.btn_run.set_enabled(not run)
        self.btn_stop.set_enabled(run)

    def _run_state_sync(self):
        """Safety net: keep the Run/Stop buttons matching self.running. If a
        race ever leaves them out of sync, this re-applies the correct state
        within a fraction of a second. Only touches the buttons on a mismatch
        so it doesn't fight the hover highlight."""
        if not self._closed:
            if self._buttons_running != self.running:
                self._set_running(self.running)
            self._sync_after = self.root.after(250, self._run_state_sync)

    def _set_highlight(self, block):
        self.highlight_block = block
        self.relayout()

    # --------------------------- Connection ------------------------------- #
    def on_connect(self):
        if self.tello:
            self.log("Already connected.")
            return
        if not TELLO_AVAILABLE:
            messagebox.showerror("djitellopy missing",
                                 "The djitellopy library is not installed.\n"
                                 "Run: pip install djitellopy")
            return
        self._set_status("● connecting...", "#cc9933")
        self.log("Connecting to Tello Wi-Fi... (join the TELLO-XXXX network)")
        threading.Thread(target=self._connect_worker, daemon=True).start()

    def _connect_worker(self):
        try:
            t = Tello()
            t.connect()
            bat = t.get_battery()
            self.tello = t
            self._ui(self._on_connected, bat)
        except Exception as e:
            self._ui(self._set_status, "● connection failed", "#cc6666")
            self._ui(self.log, "Connect error: " + str(e))

    def _on_connected(self, bat):
        self._set_status("● connected", "#66cc66")
        self._set_battery(bat)
        self.dry_run.set(False)
        self.log(f"Connected. Battery {bat}%.")

    def _battery_loop(self):
        if self.tello and not self.running:
            threading.Thread(target=self._battery_query, daemon=True).start()
        self._batt_after = self.root.after(8000, self._battery_loop)

    def _battery_query(self):
        if self.drone_lock.acquire(blocking=False):
            try:
                bat = self.tello.get_battery()
            except Exception:
                bat = None
            finally:
                self.drone_lock.release()
            if bat is not None:
                self._ui(self._set_battery, bat)

    def _set_status(self, text, color):
        self.lbl_status.config(text=text, fg=color)

    def _set_battery(self, bat):
        color = "#66cc66" if bat > 30 else ("#cccc66" if bat > 15 else "#cc6666")
        self.lbl_batt.config(text=f"battery: {bat}%", fg=color)

    # ----------------------------- File I/O ------------------------------- #
    def on_clear(self):
        if self.hat.children and not messagebox.askyesno(
                "Clear", "Remove all blocks from the script?"):
            return
        self.hat.children = []
        self.relayout()

    def on_save(self):
        path = filedialog.asksaveasfilename(defaultextension=".json",
                                            filetypes=[("Block script", "*.json")])
        if not path:
            return
        data = [c.to_dict() for c in self.hat.children]
        with open(path, "w") as f:
            json.dump(data, f, indent=2)
        self.log("Saved to " + path)

    def on_load(self):
        path = filedialog.askopenfilename(
            filetypes=[("Block script", "*.json"), ("All files", "*.*")])
        if not path:
            return
        try:
            with open(path) as f:
                data = json.load(f)
            self.hat.children = [Block.from_dict(d) for d in data]
            self.relayout()
            self.log("Loaded " + path)
        except Exception as e:
            messagebox.showerror("Load failed", str(e))

    # ----------------------------- Helpers -------------------------------- #
    def log(self, msg):
        self.log_var.set(msg)

    def _ui(self, fn, *args):
        """Queue a UI update to run on the main thread (safe to call from any
        worker thread). The work is run by _drain_ui() on the main thread."""
        if self._closed:
            return
        self._ui_queue.put((fn, args))

    def _drain_ui(self):
        """Main-thread pump: run everything workers have queued, then poll
        again. This is the only place worker-produced UI updates execute."""
        if self._closed:
            return
        while True:
            try:
                fn, args = self._ui_queue.get_nowait()
            except queue.Empty:
                break
            try:
                fn(*args)
            except tk.TclError:
                pass
        self._ui_after = self.root.after(UI_POLL_MS, self._drain_ui)

    def on_close(self):
        self._closed = True
        self.stop_flag.set()
        for after_id in (self._batt_after, self._sync_after, self._ui_after):
            if after_id is not None:
                try:
                    self.root.after_cancel(after_id)
                except Exception:
                    pass
        if self.tello:
            # Don't leave the drone hovering after the window closes.
            try:
                if self.running:
                    got = self.drone_lock.acquire(timeout=8)
                    try:
                        self.tello.land()
                    finally:
                        if got:
                            self.drone_lock.release()
            except Exception:
                pass
            try:
                self.tello.end()
            except Exception:
                pass
        self.root.destroy()


# --------------------------------------------------------------------------- #
# Flight simulation
# --------------------------------------------------------------------------- #
# World frame:  X = forward, Y = left, Z = up  (centimetres).
# Pose = [x, y, z, yaw]  with yaw in radians, CCW positive, 0 = facing +X.

def build_segments(instrs):
    """Turn a flat instruction list into timed animation segments."""
    segs = []
    pose = [0.0, 0.0, 0.0, 0.0]
    speed = DEFAULT_SPEED

    def add(kind, start, dur, label, **extra):
        # `b` is the loop variable below; add() is only called within an
        # iteration, so it refers to the block that produced this segment.
        segs.append(dict(kind=kind, start=start, end=list(pose), block=b,
                         dur=max(dur, 0.15), label=label, **extra))

    for b in instrs:
        k, P = b.kind, b.params
        start = list(pose)
        label = App.describe(b)

        if k == "speed":
            speed = max(int(P["cmps"]), 1)
            continue
        if k == "takeoff":
            pose[2] = TAKEOFF_H
            add("move", start, 2.0, label)
        elif k == "land":
            pose[2] = 0.0
            add("move", start, 2.0, label)
        elif k == "wait":
            add("hover", start, float(P["sec"]), label)
        elif k in ("cw", "ccw"):
            d = math.radians(P["deg"]) * (1 if k == "ccw" else -1)
            pose[3] += d
            add("move", start, P["deg"] / TURN_SPEED, label)
        elif k == "flip":
            add("flip", start, 1.0, label, axis=P["dir"])
        else:
            d, yaw = P.get("cm", 0), pose[3]
            if k == "forward":
                pose[0] += d * math.cos(yaw); pose[1] += d * math.sin(yaw)
            elif k == "back":
                pose[0] -= d * math.cos(yaw); pose[1] -= d * math.sin(yaw)
            elif k == "left":
                pose[0] += d * -math.sin(yaw); pose[1] += d * math.cos(yaw)
            elif k == "right":
                pose[0] -= d * -math.sin(yaw); pose[1] -= d * math.cos(yaw)
            elif k == "up":
                pose[2] += d
            elif k == "down":
                pose[2] = max(0.0, pose[2] - d)
            dist = math.dist(start[:3], pose[:3])  # actual travel (handles z clamp)
            add("move", start, dist / speed, label)
    return segs


# Tiny 3D vector helpers (tuples, no numpy needed).
def _sub(a, b): return (a[0] - b[0], a[1] - b[1], a[2] - b[2])
def _add(a, b): return (a[0] + b[0], a[1] + b[1], a[2] + b[2])
def _dot(a, b): return a[0] * b[0] + a[1] * b[1] + a[2] * b[2]
def _cross(a, b):
    return (a[1] * b[2] - a[2] * b[1],
            a[2] * b[0] - a[0] * b[2],
            a[0] * b[1] - a[1] * b[0])
def _norm(a):
    m = math.sqrt(_dot(a, a)) or 1.0
    return (a[0] / m, a[1] / m, a[2] / m)


class SimPanel(tk.Frame):
    """An in-window 3D panel that animates the planned flight."""

    def __init__(self, master, app):
        super().__init__(master, bg="#0d0d18")
        self.app = app
        self.segs = []
        self.total = 0.1

        # Camera (orbit around target)
        self.az = 35.0
        self.el = 22.0
        self.radius = 600.0
        self.focal = 700.0
        self.target = (0.0, 0.0, 60.0)

        # Playback
        self.t = 0.0
        self.playing = False
        self.speed = 1.0
        self._scrub_guard = False
        self._scrub_user_active = False
        self._hl_block = None      # block currently highlighted in the workspace

        self.font = tkfont.Font(family="Helvetica", size=10)
        self._build_ui()
        self._tick()

    def play_run(self, segs):
        """Start a dry run: load the plan and play it from the top. The sim's
        clock now drives the run — _tick advances it, the active block is
        highlighted in the workspace, and _dry_finished() fires at the end."""
        self.segs = segs
        self.total = sum(s["dur"] for s in segs) or 0.1
        self.scrub.config(to=self.total)
        self._fit_camera()
        self.t = 0.0
        self._hl_block = None
        self.playing = True
        self.redraw()

    # ------------------------------------------------------------------ UI
    def _build_ui(self):
        tk.Label(self, text="  3D SIMULATION", bg="#0d0d18", fg="#88e0ff",
                 anchor="w", font=self.font, pady=4).pack(side="top", fill="x")

        self.info = tk.StringVar(
            value="  Build a script, then press Run with Dry run checked.")
        tk.Label(self, textvariable=self.info, bg="#0d0d18", fg="#88e0ff",
                 anchor="w", font=self.font).pack(side="bottom", fill="x")

        bar = tk.Frame(self, bg="#1a1a28", pady=6, padx=6)
        bar.pack(side="bottom", fill="x")

        def btn(text, cmd, bg="#33334d"):
            return FlatButton(bar, text, cmd, bg, font=self.font)

        btn("⏮ Reset", self.reset, "#3a3a4d").pack(side="left", padx=3)
        tk.Label(bar, text="speed", bg="#1a1a28", fg="#aaa",
                 font=self.font).pack(side="left", padx=(10, 2))
        self.speed_scale = tk.Scale(bar, from_=0.25, to=3.0, resolution=0.25,
                                    orient="horizontal", length=90, bg="#1a1a28",
                                    fg="white", troughcolor="#33334d", bd=0,
                                    highlightthickness=0, showvalue=True,
                                    command=self._set_speed)
        self.speed_scale.set(1.0)
        self.speed_scale.pack(side="left")

        scrubf = tk.Frame(self, bg="#1a1a28")
        scrubf.pack(side="bottom", fill="x")
        self.scrub = tk.Scale(scrubf, from_=0.0, to=self.total, resolution=0.01,
                              orient="horizontal", bg="#1a1a28", fg="white",
                              troughcolor="#33334d", bd=0, highlightthickness=0,
                              showvalue=False, command=self._scrubbed)
        self.scrub.pack(side="left", fill="x", expand=True, padx=8)
        self.scrub.bind("<ButtonPress-1>", self._scrub_start)
        self.scrub.bind("<ButtonRelease-1>", self._scrub_stop)

        self.canvas = tk.Canvas(self, bg="#0d0d18", highlightthickness=0)
        self.canvas.pack(side="top", fill="both", expand=True)
        self.canvas.bind("<Configure>", lambda e: self.redraw())
        self.canvas.bind("<ButtonPress-1>", self._orbit_start)
        self.canvas.bind("<B1-Motion>", self._orbit_drag)
        self.canvas.bind("<MouseWheel>", self._zoom)
        self.canvas.bind("<Button-4>", lambda e: self._zoom_by(0.9))
        self.canvas.bind("<Button-5>", lambda e: self._zoom_by(1.1))

    # -------------------------------------------------------------- camera
    def _all_points(self):
        pts = [(0.0, 0.0, 0.0)]
        for s in self.segs:
            pts.append((s["end"][0], s["end"][1], s["end"][2]))
        return pts

    def _fit_camera(self):
        pts = self._all_points()
        xs = [p[0] for p in pts]; ys = [p[1] for p in pts]; zs = [p[2] for p in pts]
        cx, cy = (min(xs) + max(xs)) / 2, (min(ys) + max(ys)) / 2
        cz = (min(zs) + max(zs)) / 2
        ext = max(max(xs) - min(xs), max(ys) - min(ys),
                  max(zs) - min(zs), 200.0)
        self.target = (cx, cy, max(cz, 40.0))
        self.radius = ext * 2.2

    def _project(self, P):
        az, el = math.radians(self.az), math.radians(self.el)
        off = (self.radius * math.cos(el) * math.cos(az),
               self.radius * math.cos(el) * math.sin(az),
               self.radius * math.sin(el))
        cam = _add(self.target, off)
        fwd = _norm(_sub(self.target, cam))
        right = _norm(_cross(fwd, (0, 0, 1)))
        up = _cross(right, fwd)
        d = _sub(P, cam)
        cz = _dot(d, fwd)
        if cz <= 1.0:
            return None
        w = self.canvas.winfo_width() or 800
        h = self.canvas.winfo_height() or 560
        sx = w / 2 + self.focal * _dot(d, right) / cz
        sy = h / 2 - self.focal * _dot(d, up) / cz
        return (sx, sy, cz)

    def _orbit_start(self, e):
        self._ox, self._oy = e.x, e.y

    def _orbit_drag(self, e):
        self.az = (self.az - (e.x - self._ox) * 0.5) % 360
        self.el = max(-85, min(85, self.el + (e.y - self._oy) * 0.5))
        self._ox, self._oy = e.x, e.y
        self.redraw()

    def _zoom(self, e):
        self._zoom_by(0.9 if e.delta > 0 else 1.1)

    def _zoom_by(self, f):
        self.radius = max(80.0, min(8000.0, self.radius * f))
        self.redraw()

    # ------------------------------------------------------------ playback
    def stop(self):
        """Halt playback in place (called by the toolbar Stop)."""
        self.playing = False

    def reset(self):
        """Reset button: rewind to the start and clear the run."""
        self.playing = False
        self.t = 0.0
        self._hl_block = None
        self._scrub_guard = True
        self.scrub.set(self.t)
        self._scrub_guard = False
        self.app.on_sim_reset()
        self.redraw()

    def _set_speed(self, v):
        # Playback speed = run speed, since the sim drives the dry run.
        self.speed = float(v)

    def _scrub_start(self, _event):
        self._scrub_user_active = True

    def _scrub_stop(self, _event):
        self.after_idle(self._clear_scrub_user_active)

    def _clear_scrub_user_active(self):
        self._scrub_user_active = False

    def _scrubbed(self, v):
        if self._scrub_guard or not self._scrub_user_active:
            return
        self.t = float(v)
        self.playing = False
        # Dragging the timeline mid-run takes manual control, so end the run.
        if self.app.running and self.app.run_is_dry:
            self.app._end_run()
        self.redraw()

    def _tick(self):
        if not self.winfo_exists():
            return
        if self.playing:
            self.t += 0.033 * self.speed
            finished = self.t >= self.total
            if finished:
                self.t = self.total
                self.playing = False
            self._scrub_guard = True
            self.scrub.set(self.t)
            self._scrub_guard = False
            self.redraw()
            if finished:
                self.app._dry_finished()
        self.after(33, self._tick)

    # -------------------------------------------------------------- render
    def _pose_at(self, t):
        """Return (pose, segment, local_progress) at time t."""
        acc = 0.0
        for s in self.segs:
            if t < acc + s["dur"] or s is self.segs[-1]:
                u = (t - acc) / s["dur"] if s["dur"] else 1.0
                u = max(0.0, min(1.0, u))
                a, b = s["start"], s["end"]
                pose = [a[i] + (b[i] - a[i]) * u for i in range(4)]
                return pose, s, u
            acc += s["dur"]
        return [0, 0, 0, 0], None, 0.0

    def _draw_line(self, p1, p2, **kw):
        a, b = self._project(p1), self._project(p2)
        if a and b:
            self.canvas.create_line(a[0], a[1], b[0], b[1], **kw)

    def _follow_highlight(self, seg):
        """During a dry run, highlight the block whose segment is playing now,
        and log it. Only acts when the active block changes, so the workspace
        is relaid out a handful of times per run, not every frame."""
        if not (self.app.running and self.app.run_is_dry):
            return
        block = seg.get("block") if seg else None
        if block is self._hl_block:
            return
        self._hl_block = block
        self.app._set_highlight(block)
        if block is not None:
            self.app.log("(dry) " + App.describe(block))

    def redraw(self):
        c = self.canvas
        c.delete("all")
        self._draw_floor()
        if not self.segs:
            self._draw_drone([0.0, 0.0, 0.0, 0.0], None, 0.0)
            return
        self._draw_path()
        pose, seg, u = self._pose_at(self.t)
        self._draw_drone(pose, seg, u)
        self._follow_highlight(seg)
        cmd = seg["label"] if seg else "done"
        self.info.set(
            f"  t={self.t:4.1f}/{self.total:.1f}s   "
            f"x={pose[0]:.0f}  y={pose[1]:.0f}  z={pose[2]:.0f} cm   "
            f"yaw={math.degrees(pose[3]) % 360:.0f}°    ▸ {cmd}")

    def _draw_floor(self):
        g, step = 300, 50
        # grow grid to cover the path
        for p in self._all_points():
            g = max(g, int(abs(p[0])) + 100, int(abs(p[1])) + 100)
        g = (g // step + 1) * step
        for i in range(-g, g + 1, step):
            self._draw_line((i, -g, 0), (i, g, 0), fill="#1d2740")
            self._draw_line((-g, i, 0), (g, i, 0), fill="#1d2740")
        # axes
        self._draw_line((0, 0, 0), (g, 0, 0), fill="#664444", width=2)  # +X fwd
        self._draw_line((0, 0, 0), (0, g, 0), fill="#446644", width=2)  # +Y left

    def _draw_path(self):
        pts = self._all_points()
        for i in range(len(pts) - 1):
            self._draw_line(pts[i], pts[i + 1], fill="#3a6ea5", width=2)
        for p in pts[1:]:
            pr = self._project(p)
            if pr:
                self.canvas.create_oval(pr[0] - 3, pr[1] - 3, pr[0] + 3,
                                        pr[1] + 3, fill="#3a6ea5", outline="")

    def _transform(self, local, pose, seg, u):
        x, y, z = local
        # flip animation (roll/pitch about body axis), position unchanged
        if seg and seg["kind"] == "flip":
            ang = u * 2 * math.pi
            ca, sa = math.cos(ang), math.sin(ang)
            if seg["axis"] in ("forward", "back"):  # pitch about Y
                s = 1 if seg["axis"] == "forward" else -1
                x, z = x * ca + z * sa * s, -x * sa * s + z * ca
            else:                                    # roll about X
                s = -1 if seg["axis"] == "left" else 1
                y, z = y * ca - z * sa * s, y * sa * s + z * ca
        yaw = pose[3]
        cy, sy = math.cos(yaw), math.sin(yaw)
        wx = x * cy - y * sy + pose[0]
        wy = x * sy + y * cy + pose[1]
        wz = z + pose[2]
        return (wx, wy, wz)

    def _draw_drone(self, pose, seg, u):
        a, r = 16, 8  # arm reach, rotor radius
        arms = [(a, a, 0), (a, -a, 0), (-a, a, 0), (-a, -a, 0)]
        center = self._transform((0, 0, 0), pose, seg, u)
        # shadow on the ground + altitude line
        gp = self._project((pose[0], pose[1], 0))
        cp = self._project(center)
        if gp:
            self.canvas.create_oval(gp[0] - 10, gp[1] - 5, gp[0] + 10, gp[1] + 5,
                                    outline="#222", fill="#05050a")
        if gp and cp:
            self.canvas.create_line(cp[0], cp[1], gp[0], gp[1], fill="#2a3a55",
                                    dash=(3, 3))
        # arms + rotors
        for arm in arms:
            w = self._transform(arm, pose, seg, u)
            self._draw_line(center, w, fill="#ff7733", width=3)
            wp = self._project(w)
            if wp:
                rad = max(3, min(60, self.focal * r / wp[2]))
                self.canvas.create_oval(wp[0] - rad, wp[1] - rad, wp[0] + rad,
                                        wp[1] + rad, outline="#ffaa66", width=2)
        # body
        if cp:
            self.canvas.create_oval(cp[0] - 6, cp[1] - 6, cp[0] + 6, cp[1] + 6,
                                    fill="#ff3b30", outline="white")
        # heading marker (points forward)
        nose = self._transform((a + 10, 0, 0), pose, seg, u)
        self._draw_line(center, nose, fill="#ffe066", width=3)


def main():
    root = tk.Tk()
    App(root)
    root.mainloop()


if __name__ == "__main__":
    main()
