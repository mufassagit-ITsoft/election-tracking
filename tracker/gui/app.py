# This is where the graphics, the map data and the interacive elements are tied in all together.
import queue
import threading
import tkinter as tk
from tkinter import ttk
 
from django.db import close_old_connections
 
from tracker import poller as poll, services
from tracker.models import Election
from . import mapdata as md
 
__author__ = "Mustafa Prodhan"
SMALL = {"RI", "DE", "DC", "VT", "NH", "NJ", "CT", "MA", "MD"}   # too small for an in-map label
PAD = 14
 
 
class MapApp:
    def __init__(self, root, year=2026):
        self.root, self.year = root, year
        root.title(f"Election Tracker - {year} midterms")
        root.geometry("1280x780")
        self.office = tk.StringVar(value="senate")
        self.mode = tk.StringVar(value="rating")
        self.rater = tk.StringVar(value="cook")
        self.bg = tk.BooleanVar(value=True)
        self.poller = None
        self.lock = poll.Lock()
        self.pstatus = tk.StringVar(value="")
        self.status = tk.StringVar(value="")
        self.items = {}        # canvas item id -> key
        self.data = {}
        self.selected = None
        self.q = queue.Queue()
        self._busy = False
        self._shapes = None
        self._build()
        self.reload()
        self._toggle_poller()
        self._tick()
        root.protocol("WM_DELETE_WINDOW", self._close)
 
    # background poller
    def _toggle_poller(self):
        if self.bg.get() and not self.poller:
            if self.lock.acquire():
                self.poller = poll.Poller(self.year)
                self.poller.start_thread()
            else:
                self.pstatus.set("Poller already running in another process (run_poller); showing its data")
                return
        elif not self.bg.get() and self.poller:
            self.poller.stop.set()
            self.poller = None
            self.lock.release()
        self._poller_line()
 
    def _poller_line(self):
        if self.poller:
            who = "Poller ON (this window)"
        elif self.lock.fh is None and self.bg.get():
            who = "Poller running in another process"
        else:
            who = "Poller OFF"
        close_old_connections()
        bits = []
        for job, st in poll.status().items():
            bits.append(f"{job} {st['at'].astimezone():%H:%M:%S}" + ("" if st["ok"] else " FAILED"))
        self.pstatus.set(who + ("   |   last run: " + ", ".join(bits) if bits else "   |   no runs yet"))
 
    def _close(self):
        if self.poller:
            self.poller.stop.set()
        self.lock.release()
        self.root.destroy()
 
    # layout
    def _build(self):
        top = ttk.Frame(self.root, padding=6)
        top.pack(fill="x")
        for label, var, values in (("Office", self.office, md.OFFICES), ("View", self.mode, md.MODES),
                                   ("Rater", self.rater, md.RATERS)):
            ttk.Label(top, text=label).pack(side="left", padx=(8, 2))
            cb = ttk.Combobox(top, textvariable=var, values=values, width=9, state="readonly")
            cb.pack(side="left")
            cb.bind("<<ComboboxSelected>>", lambda e: self.reload())
        ttk.Button(top, text="Reload", command=self.reload).pack(side="left", padx=(14, 2))
        self.pull_btn = ttk.Button(top, text="Pull from CivicAPI", command=self.pull)
        self.pull_btn.pack(side="left", padx=2)
        ttk.Checkbutton(top, text="Background poller", variable=self.bg,
                        command=self._toggle_poller).pack(side="left", padx=(12, 0))
        ttk.Label(top, text="Data: civicapi.org", foreground="#666").pack(side="right")
 
        body = ttk.Frame(self.root)
        body.pack(fill="both", expand=True)
        self.canvas = tk.Canvas(body, bg="white", highlightthickness=0)
        self.canvas.pack(side="left", fill="both", expand=True)
        self.detail = tk.Text(body, width=46, wrap="word", font=("TkFixedFont", 10), padx=8, pady=8)
        self.detail.pack(side="right", fill="y")
        ttk.Label(self.root, textvariable=self.pstatus, relief="sunken", anchor="w", padding=3).pack(fill="x")
        ttk.Label(self.root, textvariable=self.status, relief="sunken", anchor="w", padding=3).pack(fill="x")
        self.canvas.bind("<Configure>", lambda e: self.draw())
        self.canvas.bind("<Motion>", self._hover)
        self.canvas.bind("<Button-1>", self._click)
 
    # data
    def reload(self):
        close_old_connections()
        self.data = md.layer_data(self.year, self.office.get(), self.mode.get(), self.rater.get())
        self.status.set(md.summary_line(self.year, self.office.get(), self.mode.get(), self.rater.get()))
        self.draw()
 
    def pull(self):
        if self._busy:
            return
        self._busy = True
        self.pull_btn.state(["disabled"])
        self.status.set("Pulling from CivicAPI ...")
        threading.Thread(target=self._pull_worker, daemon=True).start()
 
    def _pull_worker(self):
        try:
            el = Election.objects.filter(year=self.year).first()
            stored, unmatched = services.sync_results(el)
            early = services.sync_early_vote(el)
            self.q.put(f"Pulled: {stored} races, {len(unmatched)} unmatched, {early} states early vote")
        except Exception as exc:
            self.q.put(f"Pull failed: {exc!r}")
        finally:
            close_old_connections()
 
    def _tick(self):
        try:
            while True:
                msg = self.q.get_nowait()
                self._busy = False
                self.pull_btn.state(["!disabled"])
                self.reload()
                self.status.set(msg)
        except queue.Empty:
            pass
        self._n = getattr(self, "_n", 0) + 1
        if self._n % 10 == 0:                 # every 10 s re-read SQLite (the poller writes it)
            self._poller_line()
            if not self._busy:
                self.reload()
                self._show_detail(self.selected)
        self.root.after(1000, self._tick)
 
    # drawing
    def draw(self):
        c = self.canvas
        c.delete("all")
        self.items.clear()
        w, h = max(c.winfo_width(), 200), max(c.winfo_height(), 200)
        legend_h = 34
        if self.office.get() == "house":
            self._draw_tiles(w, h - legend_h)
        else:
            self._draw_states(w, h - legend_h)
        self._draw_legend(w, h)
        if self.selected:
            self._outline(self.selected)
 
    def _fit(self, minx, miny, maxx, maxy, w, h):
        s = min((w - 2 * PAD) / (maxx - minx), (h - 2 * PAD) / (maxy - miny))
        ox = (w - (maxx - minx) * s) / 2 - minx * s
        oy = (h - (maxy - miny) * s) / 2 - miny * s
        return s, ox, oy
 
    def _draw_states(self, w, h):
        if self._shapes is None:
            self._shapes = md.project_states()
        shapes = self._shapes
        if not shapes:
            self.canvas.create_text(w / 2, h / 2, text="No map shapes yet. Run: python manage.py load_geo")
            return
        pts = [p for rings in shapes.values() for ring in rings for p in ring]
        s, ox, oy = self._fit(min(p[0] for p in pts), min(p[1] for p in pts),
                              max(p[0] for p in pts), max(p[1] for p in pts), w, h)
        for key, rings in shapes.items():
            d = self.data.get(key)
            fill = d["fill"] if d else md.NO_DATA
            for ring in rings:
                flat = [v for x, y in ring for v in (x * s + ox, y * s + oy)]
                i = self.canvas.create_polygon(flat, fill=fill, outline="white", width=1)
                self.items[i] = key
            if key not in SMALL:
                xs = [x for ring in rings for x, _ in ring]
                ys = [y for ring in rings for _, y in ring]
                self.canvas.create_text((min(xs) + max(xs)) / 2 * s + ox, (min(ys) + max(ys)) / 2 * s + oy,
                                        text=key, font=("TkDefaultFont", 8, "bold"), fill="#222", state="disabled")
 
    def _draw_tiles(self, w, h):
        tiles = md.tile_squares()
        if not tiles:
            self.canvas.create_text(w / 2, h / 2, text="No House tiles yet. Run: python manage.py load_geo")
            return
        xs = [t[0] for t in tiles.values()]
        ys = [t[1] for t in tiles.values()]
        s, ox, oy = self._fit(min(xs), min(ys), max(xs) + 1, max(ys) + 1, w, h)
        for key, (x, y, sz) in tiles.items():
            d = self.data.get(key)
            i = self.canvas.create_rectangle(x * s + ox, y * s + oy, (x + sz) * s + ox, (y + sz) * s + oy,
                                             fill=d["fill"] if d else md.NO_DATA, outline="white")
            self.items[i] = key
        by_state = {}
        for key, (x, y, _) in tiles.items():
            by_state.setdefault(key.split("-")[0], []).append((x, y))
        for st, pts in by_state.items():
            cx = (min(p[0] for p in pts) + max(p[0] for p in pts) + 1) / 2
            ty = min(p[1] for p in pts) - 0.1
            self.canvas.create_text(cx * s + ox, ty * s + oy, text=st, anchor="s",
                                    font=("TkDefaultFont", 8, "bold"), fill="#222", state="disabled")
 
    def _draw_legend(self, w, h):
        items = md.legend(self.mode.get())
        x, y = PAD, h - 24
        for label, color in items:
            self.canvas.create_rectangle(x, y, x + 14, y + 14, fill=color, outline="#999")
            t = self.canvas.create_text(x + 19, y + 7, text=label, anchor="w", font=("TkDefaultFont", 9))
            x = self.canvas.bbox(t)[2] + 14
 
    # interaction
    def _key_at(self, ev):
        hit = self.canvas.find_overlapping(ev.x, ev.y, ev.x, ev.y)
        for i in reversed(hit):
            if i in self.items:
                return self.items[i]
        return None
 
    def _hover(self, ev):
        k = self._key_at(ev)
        if k and k in self.data:
            self.status.set(self.data[k]["tip"])
 
    def _click(self, ev):
        k = self._key_at(ev)
        if not k:
            return
        self.selected = k
        self._outline(k)
        self._show_detail(k)
 
    def _show_detail(self, k):
        if not k:
            return
        d = self.data.get(k)
        self.detail.delete("1.0", "end")
        if d and d["race_id"]:
            text = md.race_detail(d["race_id"])
        elif d:
            text = md.state_early_detail(self.year, d.get("state", k))
        else:
            text = f"{k}: no {self.office.get()} race"
        self.detail.insert("1.0", text)
 
    def _outline(self, key):
        for i, k in self.items.items():
            self.canvas.itemconfigure(i, outline="black" if k == key else "white", width=2 if k == key else 1)
            if k == key:
                self.canvas.tag_raise(i)
 
 
def run(year=2026):
    root = tk.Tk()
    MapApp(root, year)
    root.mainloop()