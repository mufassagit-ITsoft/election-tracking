import queue
import threading
import tkinter as tk
from tkinter import ttk
 
from django.db import OperationalError, close_old_connections
 
from tracker import poller as poll, services
from tracker.models import Election
from . import mapdata as md
 
__author__ = "Mustafa Prodhan"
SMALL = {"RI", "DE", "DC", "VT", "NH", "NJ", "CT", "MA", "MD"}   # too small for an in-map label
PAD = 14
TALLY_H = 124
T_BG, T_CARD, T_EDGE, T_TRACK = "#070b14", "#0f1729", "#1e2b4a", "#1a2440"
T_D, T_R, T_O = "#3aa0ff", "#ff4d5e", "#b58cff"
T_D_DIM, T_R_DIM = "#1c4f80", "#80303b"
T_TEXT, T_QUIET, T_ACCENT = "#e8efff", "#7f93bd", "#35e0ff"
 
 
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
        self.seats = {}
        self.selected = None
        self.q = queue.Queue()
        self._busy = False
        self._shapes = None
        self._build()
        self.reload()
        self._toggle_poller()
        self._tick()
        root.protocol("WM_DELETE_WINDOW", self._close)
 
    # ---- background poller
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
        try:
            statuses = poll.status()
        except OperationalError:
            statuses = {}
        for job, st in statuses.items():
            bits.append(f"{job} {st['at']:%H:%M:%S}" + ("" if st["ok"] else " FAILED"))
        warn = ""
        if "test_data=True" in (statuses.get("results", {}).get("detail") or ""):
            warn = "*** TEST DATA (CivicAPI rehearsal, not real results) ***   "
        self.pstatus.set(warn + who + ("   |   last run (UTC): " + ", ".join(bits) if bits else "   |   no runs yet"))
 
    def _close(self):
        if self.poller:
            self.poller.stop.set()
        self.lock.release()
        self.root.destroy()
 
    # ---- layout
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
 
        self.tally = tk.Canvas(self.root, height=TALLY_H, bg=T_BG, highlightthickness=0)
        self.tally.pack(fill="x")
        self.tally.bind("<Configure>", lambda e: self.draw_tally())
        self.tally.bind("<Button-1>", self._tally_click)
 
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
 
    # ---- data
    def reload(self):
        close_old_connections()
        try:
            self.data = md.layer_data(self.year, self.office.get(), self.mode.get(), self.rater.get())
            self.seats = md.seat_tally(self.year)
            self.status.set(md.summary_line(self.year, self.office.get(), self.mode.get(), self.rater.get()))
        except OperationalError:   # tables not there yet: container still migrating, or wrong DB_DIR
            self.data, self.seats = {}, {}
            self.status.set("Database not ready (no tables yet). Wait for the container to finish "
                            "'migrate + load_seed', or run them. Retrying every 10 s.")
        self.draw()
        self.draw_tally()
 
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
 
    # ---- drawing
    def draw(self):
        c = self.canvas
        c.delete("all")
        self.items.clear()
        w, h = max(c.winfo_width(), 200), max(c.winfo_height(), 200)
        legend_h = 34
        try:
            if self.office.get() == "house":
                self._draw_tiles(w, h - legend_h)
            else:
                self._draw_states(w, h - legend_h)
        except OperationalError:
            c.create_text(w / 2, (h - legend_h) / 2, text="Database not ready (no tables yet)", fill="#666")
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
 
    # ---- seat tracker (seats won on election night, from the newest snapshot of every race)
    def draw_tally(self):
        c = self.tally
        c.delete("all")
        w = max(c.winfo_width(), 300)
        gap = 10
        pw = (w - gap * 4) / 3
        self._panels = []
        for i, office in enumerate(("senate", "house", "governor")):
            x0, y0, x1, y1 = gap + i * (pw + gap), 8, gap + i * (pw + gap) + pw, TALLY_H - 8
            self._panels.append((x0, x1, office))
            self._draw_panel(c, office, x0, y0, x1, y1, self.seats.get(office))
 
    def _draw_panel(self, c, office, x0, y0, x1, y1, t):
        on = office == self.office.get()
        c.create_rectangle(x0, y0, x1, y1, fill=T_CARD, outline=T_ACCENT if on else T_EDGE, width=2 if on else 1)
        c.create_line(x0 + 1, y0, x1 - 1, y0, fill=T_ACCENT if on else T_EDGE, width=2)      # top glow rail
        c.create_text(x0 + 14, y0 + 13, text=" ".join(office.upper()), anchor="w", fill=T_QUIET,
                      font=("TkDefaultFont", 8, "bold"))
        if not t or not t["total"]:
            c.create_text((x0 + x1) / 2, (y0 + y1) / 2 + 6, text="no data", fill=T_QUIET)
            return
        c.create_text(x1 - 14, y0 + 13, text=f"{t['called']}/{t['up']} CALLED", anchor="e", fill=T_QUIET,
                      font=("TkDefaultFont", 8, "bold"))
        if t["majority"]:
            c.create_text((x0 + x1) / 2, y0 + 13, text=f"{t['majority']} TO WIN", fill=T_TEXT,
                          font=("TkDefaultFont", 8, "bold"))
        big = ("TkDefaultFont", 30, "bold")
        n = c.create_text(x0 + 14, y0 + 66, text=str(t["d"]), anchor="sw", fill=T_D, font=big)
        c.create_text(c.bbox(n)[2] + 6, y0 + 63, text="DEM", anchor="sw", fill=T_D, font=("TkDefaultFont", 9, "bold"))
        n = c.create_text(x1 - 14, y0 + 66, text=str(t["r"]), anchor="se", fill=T_R, font=big)
        c.create_text(c.bbox(n)[0] - 6, y0 + 63, text="GOP", anchor="se", fill=T_R, font=("TkDefaultFont", 9, "bold"))
        if t["o"]:
            c.create_text((x0 + x1) / 2, y0 + 48, text=f"IND {t['o']}", fill=T_O, font=("TkDefaultFont", 10, "bold"))
        elif t["ind"]:                       # independents are counted inside the Democratic total
            c.create_text(x0 + 14, y0 + 76, text=f"incl. {t['ind']} IND", anchor="w", fill=T_O,
                          font=("TkDefaultFont", 8, "bold"))
        lead = []
        if t["lead_d"]:
            lead.append(f"DEM +{t['lead_d']}")
        if t["lead_r"]:
            lead.append(f"GOP +{t['lead_r']}")
        c.create_text(x1 - 14, y0 + 76, anchor="e", text=("leading: " + "  ".join(lead)) if lead else "", fill=T_QUIET,
                      font=("TkDefaultFont", 8))
        # bar: won seats solid, leading (uncalled) dim, remainder open
        bx0, bx1, by0, by1 = x0 + 14, x1 - 14, y1 - 20, y1 - 10
        bw, tot = bx1 - bx0, float(t["total"])
        c.create_rectangle(bx0, by0, bx1, by1, fill=T_TRACK, outline="")
        left = bx0
        for cnt, col in ((t["d"], T_D), (t["o"], T_O), (t["lead_d"], T_D_DIM)):
            if cnt:
                c.create_rectangle(left, by0, left + bw * cnt / tot, by1, fill=col, outline="")
                left += bw * cnt / tot
        right = bx1
        for cnt, col in ((t["r"], T_R), (t["lead_r"], T_R_DIM)):
            if cnt:
                c.create_rectangle(right - bw * cnt / tot, by0, right, by1, fill=col, outline="")
                right -= bw * cnt / tot
        if t["majority"]:                                   # majority tick
            mx = bx0 + bw * (t["majority"] - 0.5) / tot
            c.create_line(mx, by0 - 5, mx, by1 + 5, fill=T_TEXT, width=2)
 
    def _tally_click(self, ev):
        for x0, x1, office in getattr(self, "_panels", []):
            if x0 <= ev.x <= x1 and office != self.office.get():
                self.office.set(office)
                self.selected = None
                self.reload()
                return
 
    # ---- interaction
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