"""Окно «План бункера»: комнаты, объекты, проходы и живые фишки персонажей.

Всё перетаскивается мышью. Фишки показывают, кто где: их двигает сценарист по ходу сцен,
а вручную их можно перетащить в нужную комнату (или в верхнюю полосу «вне плана»)."""
from __future__ import annotations

import tkinter as tk
from tkinter import messagebox, simpledialog, ttk

from ui import ACCENT, BG, FG, FIELD, MUTED, PANEL, F, mk_text, modal, safe
from world import GRID_H, GRID_W, border_point, norm, parse_visibility, visibility_text

CELL, TOP = 28, 44          # размер клетки, высота верхней полосы «вне плана»
TOKEN_R = 11                # радиус фишки персонажа
CANVAS_BG, GRID_LINE = "#161918", "#222725"
OBJECT_FILL, HIDDEN_FILL, LINK_COLOR = "#46504a", "#2b302d", "#b08a3c"


class PlanWindow:
    def __init__(self, app):
        self.app = app
        self.sel = None            # ("room", id) или ("object", id)
        self.drag = None
        self.token_boxes = []      # (имя, x0, y0, x1, y1) — для попадания мышью
        self.top = top = tk.Toplevel(app.root)
        top.title(f"План бункера — {app.session.name}")
        top.configure(bg=BG)
        top.geometry("1340x780")
        top.protocol("WM_DELETE_WINDOW", self.close)

        bar = ttk.Frame(top)
        bar.pack(fill="x", padx=12, pady=(12, 6))
        ttk.Button(bar, text="+ Комната", command=self.add_room).pack(side="left")
        ttk.Button(bar, text="+ Объект в выбранную комнату", command=self.add_object).pack(side="left", padx=6)
        ttk.Label(bar, style="Muted.TLabel",
                  text="Комнаты, объекты и фишки персонажей перетаскиваются мышью.").pack(side="left", padx=12)

        body = ttk.Frame(top)
        body.pack(fill="both", expand=True, padx=12, pady=(0, 12))
        body.columnconfigure(0, weight=1)
        body.rowconfigure(0, weight=1)
        self.cv = cv = tk.Canvas(body, bg=CANVAS_BG, highlightthickness=0,
                                 scrollregion=(0, 0, GRID_W * CELL + 2, TOP + GRID_H * CELL + 2))
        xs = ttk.Scrollbar(body, orient="horizontal", command=cv.xview)
        ys = ttk.Scrollbar(body, orient="vertical", command=cv.yview)
        cv.configure(xscrollcommand=xs.set, yscrollcommand=ys.set)
        cv.grid(row=0, column=0, sticky="nsew")
        ys.grid(row=0, column=1, sticky="ns")
        xs.grid(row=1, column=0, sticky="ew")
        cv.bind("<Button-1>", self.on_press)
        cv.bind("<B1-Motion>", self.on_motion)
        cv.bind("<ButtonRelease-1>", self.on_release)

        side = ttk.Frame(body, padding=(14, 0, 0, 0))
        side.grid(row=0, column=2, rowspan=2, sticky="ns")
        self._build_form(side)
        self.redraw()
        self.fill_form()

    # ------------------------------------------------------------------ доступ к данным

    @property
    def s(self):
        return self.app.session

    @property
    def world(self):
        return self.app.session.world

    # ------------------------------------------------------------------ форма справа

    def _build_form(self, side):
        side.configure(width=330)
        self.title = ttk.Label(side, text="", style="Head.TLabel")
        self.title.pack(anchor="w")
        self.hint = ttk.Label(side, style="Muted.TLabel", justify="left", wraplength=310,
                              text="Выберите комнату или объект, чтобы описать их. Двигайте мышью: комнату "
                                   "целиком, объект внутри комнаты или в другую.\n\nФишки персонажей можно "
                                   "перетащить в комнату. Верхняя полоса означает «вне плана».\n\nЧтобы связать "
                                   "комнаты, выберите комнату и нажмите «Связать с…».")
        self.hint.pack(anchor="w", pady=(10, 0))
        f = self.form = ttk.Frame(side)
        f.columnconfigure(0, weight=1)

        def label(text, row):
            ttk.Label(f, text=text, style="Muted.TLabel", wraplength=300, justify="left").grid(
                row=row, column=0, sticky="w", pady=(10, 0))

        label("Название", 0)
        self.f_name = ttk.Entry(f)
        self.f_name.grid(row=1, column=0, sticky="ew")
        label("Описание", 2)
        self.f_desc = mk_text(f, 4)
        self.f_desc.grid(row=3, column=0, sticky="ew")

        self.obj_box = ob = ttk.Frame(f)
        ob.grid(row=4, column=0, sticky="ew")
        ob.columnconfigure(0, weight=1)
        ttk.Label(ob, text="Состояние (меняется по сюжету)", style="Muted.TLabel").grid(
            row=0, column=0, sticky="w", pady=(10, 0))
        self.f_state = ttk.Entry(ob)
        self.f_state.grid(row=1, column=0, sticky="ew")
        ttk.Label(ob, text="Кто видит: пусто — все, «автор» — только автор, иначе имена через запятую",
                  style="Muted.TLabel", wraplength=300, justify="left").grid(row=2, column=0, sticky="w", pady=(10, 0))
        self.f_vis = ttk.Entry(ob)
        self.f_vis.grid(row=3, column=0, sticky="ew")

        size = ttk.Frame(f)
        size.grid(row=5, column=0, sticky="w", pady=(10, 0))
        self.v_w, self.v_h = tk.IntVar(value=1), tk.IntVar(value=1)
        ttk.Label(size, text="Размер в клетках:", style="Muted.TLabel").pack(side="left")
        ttk.Spinbox(size, from_=1, to=GRID_W, width=4, textvariable=self.v_w).pack(side="left", padx=(8, 2))
        ttk.Label(size, text="×", style="Muted.TLabel").pack(side="left")
        ttk.Spinbox(size, from_=1, to=GRID_H, width=4, textvariable=self.v_h).pack(side="left", padx=2)

        btns = ttk.Frame(f)
        btns.grid(row=6, column=0, sticky="ew", pady=(12, 0))
        ttk.Button(btns, text="Применить", style="Accent.TButton", command=self.apply_form).pack(side="left")
        ttk.Button(btns, text="Удалить", command=self.delete_selected).pack(side="left", padx=8)

        self.room_box = rb = ttk.Frame(f)
        rb.grid(row=7, column=0, sticky="ew")
        rb.columnconfigure(0, weight=1)
        self.lbl_here = ttk.Label(rb, text="", style="Muted.TLabel", wraplength=300, justify="left")
        self.lbl_here.grid(row=0, column=0, sticky="w", pady=(14, 0))
        ttk.Label(rb, text="Проходы", style="Muted.TLabel").grid(row=1, column=0, sticky="w", pady=(10, 2))
        self.f_links = tk.Listbox(rb, height=5, exportselection=False, activestyle="none", bg=FIELD, fg=FG,
                                  selectbackground=ACCENT, selectforeground=BG, relief="flat",
                                  highlightthickness=0, font=F.base)
        self.f_links.grid(row=2, column=0, sticky="ew")
        lb = ttk.Frame(rb)
        lb.grid(row=3, column=0, sticky="w", pady=(6, 0))
        ttk.Button(lb, text="Связать с…", command=self.add_link).pack(side="left")
        ttk.Button(lb, text="Убрать проход", command=self.remove_link).pack(side="left", padx=8)
        self._link_rooms = []

    def fill_form(self):
        w = self.world
        item = None
        if self.sel:
            kind, ident = self.sel
            item = w.room_by_id(ident) if kind == "room" else w.object_by_id(ident)
            if not item:
                self.sel = None
        if not item:
            self.form.pack_forget()
            self.title.config(text="План бункера")
            self.hint.pack(anchor="w", pady=(10, 0))
            return
        self.hint.pack_forget()
        self.form.pack(fill="x", pady=(6, 0))
        is_room = self.sel[0] == "room"
        self.title.config(text="Комната" if is_room else "Объект")
        self.f_name.delete(0, "end")
        self.f_name.insert(0, item["name"])
        self.f_desc.delete("1.0", "end")
        self.f_desc.insert("1.0", item.get("description", ""))
        self.v_w.set(item["w"])
        self.v_h.set(item["h"])
        if is_room:
            self.obj_box.grid_remove()
            self.room_box.grid()
            near = ", ".join(w.occupants(item["id"], order=self.s.order)) or "никого"
            self.lbl_here.config(text="Сейчас здесь: " + safe(near))
            self.f_links.delete(0, "end")
            self._link_rooms = []
            for r, lab in w.neighbors(item["id"]):
                self.f_links.insert("end", safe(f"→ {r['name']}" + (f" ({lab})" if lab else "")))
                self._link_rooms.append(r["id"])
        else:
            self.room_box.grid_remove()
            self.obj_box.grid()
            self.f_state.delete(0, "end")
            self.f_state.insert(0, item.get("state", ""))
            self.f_vis.delete(0, "end")
            v = item.get("visibility", "all")
            self.f_vis.insert(0, "" if v == "all" else ("автор" if v == "author" else visibility_text(v)))

    def refresh(self):
        self.redraw()
        self.fill_form()

    def commit(self):
        self.s.save_world()
        self.redraw()
        self.fill_form()
        self.app.world_changed(self)

    # ------------------------------------------------------------------ действия

    def select(self, sel):
        self.sel = sel
        self.fill_form()
        self.redraw()

    def add_room(self):
        name = simpledialog.askstring("Новая комната", "Название комнаты:", parent=self.top)
        if not name:
            return
        try:
            r = self.world.add_room(name)
        except ValueError as e:
            messagebox.showerror("Комната", str(e), parent=self.top)
            return
        self.sel = ("room", r["id"])
        self.commit()

    def _current_room(self):
        w = self.world
        if self.sel and self.sel[0] == "room":
            return w.room_by_id(self.sel[1])
        if self.sel and self.sel[0] == "object":
            o = w.object_by_id(self.sel[1])
            return w.room_by_id(o["room"]) if o else None
        return w.rooms[0] if len(w.rooms) == 1 else None

    def add_object(self):
        room = self._current_room()
        if not room:
            messagebox.showinfo("Объект", "Сначала выберите комнату, в которую его поставить.", parent=self.top)
            return
        name = simpledialog.askstring("Новый объект", f"Название объекта в «{room['name']}»:", parent=self.top)
        if not name:
            return
        try:
            o = self.world.add_object(room["id"], name, state="в порядке")
        except ValueError as e:
            messagebox.showerror("Объект", str(e), parent=self.top)
            return
        self.sel = ("object", o["id"])
        self.commit()

    def apply_form(self):
        if not self.sel:
            return
        w, (kind, ident) = self.world, self.sel
        name = " ".join(self.f_name.get().split())
        desc = self.f_desc.get("1.0", "end").strip()
        try:
            wd, ht = int(self.v_w.get()), int(self.v_h.get())
        except (ValueError, tk.TclError):
            messagebox.showerror("Размер", "Размер задаётся целыми числами.", parent=self.top)
            return
        if kind == "room":
            r = w.room_by_id(ident)
            if not name or any(norm(x["name"]) == norm(name) and x["id"] != ident for x in w.rooms):
                messagebox.showerror("Комната", "Название пустое или уже занято.", parent=self.top)
                return
            r["name"], r["description"] = name, desc
            w.resize_room(ident, wd, ht)
        else:
            o = w.object_by_id(ident)
            if not name or any(norm(x["name"]) == norm(name) and x["id"] != ident for x in w.objects_in(o["room"])):
                messagebox.showerror("Объект", "Название пустое или уже занято в этой комнате.", parent=self.top)
                return
            o["name"], o["description"] = name, desc
            o["state"] = self.f_state.get().strip()
            o["visibility"] = parse_visibility(self.f_vis.get())
            w.resize_object(ident, wd, ht)
        self.commit()

    def delete_selected(self):
        if not self.sel:
            return
        w, (kind, ident) = self.world, self.sel
        item = w.room_by_id(ident) if kind == "room" else w.object_by_id(ident)
        if not item:
            return
        what = "комнату вместе с объектами и проходами" if kind == "room" else "объект"
        if not messagebox.askyesno("Удалить", f"Удалить {what} «{item['name']}»?", parent=self.top):
            return
        w.delete_room(ident) if kind == "room" else w.delete_object(ident)
        self.sel = None
        self.commit()

    def add_link(self):
        if not self.sel or self.sel[0] != "room":
            return
        w = self.world
        room = w.room_by_id(self.sel[1])
        others = [r["name"] for r in w.rooms if r["id"] != room["id"]]
        if not others:
            messagebox.showinfo("Проход", "Нужна ещё хотя бы одна комната.", parent=self.top)
            return
        top = modal(self.app, "Проход между комнатами", 440, 250)
        target, label = tk.StringVar(value=others[0]), tk.StringVar(value="дверь")
        ttk.Label(top, text=f"Связать «{room['name']}» с комнатой:").pack(anchor="w", padx=14, pady=(14, 4))
        ttk.Combobox(top, textvariable=target, values=others, state="readonly").pack(fill="x", padx=14)
        ttk.Label(top, text="Название прохода (дверь, люк, коридор…)", style="Muted.TLabel").pack(
            anchor="w", padx=14, pady=(12, 4))
        ttk.Entry(top, textvariable=label).pack(fill="x", padx=14)

        ttk.Button(top, text="Связать", style="Accent.TButton",
                   command=lambda: self.do_link(top, room["id"], target.get(), label.get())).pack(pady=16)

    def do_link(self, top, room_id, target, label):
        try:
            self.world.link(room_id, target, label)
        except ValueError as e:
            messagebox.showerror("Проход", str(e), parent=top)
            return
        top.destroy()
        self.commit()

    def remove_link(self):
        sel = self.f_links.curselection()
        if not sel or not self.sel or self.sel[0] != "room":
            return
        self.world.unlink(self.sel[1], self._link_rooms[sel[0]])
        self.commit()

    # ------------------------------------------------------------------ мышь

    def _pos(self, e):
        x, y = self.cv.canvasx(e.x), self.cv.canvasy(e.y)
        return x, y, x / CELL, (y - TOP) / CELL

    def on_press(self, e):
        x, y, gx, gy = self._pos(e)
        w = self.world
        self.drag = None
        for name, x0, y0, x1, y1 in reversed(self.token_boxes):
            if x0 <= x <= x1 and y0 <= y <= y1:
                self.drag = {"kind": "token", "id": name, "pos": (x, y)}
                self.redraw()
                return
        o = w.object_at(gx, gy)
        if o:
            self.drag = {"kind": "object", "id": o["id"], "ox": gx - o["x"], "oy": gy - o["y"]}
            self.select(("object", o["id"]))
            return
        r = w.room_at(gx, gy)
        if r:
            self.drag = {"kind": "room", "id": r["id"], "ox": gx - r["x"], "oy": gy - r["y"]}
            self.select(("room", r["id"]))
            return
        self.select(None)

    def on_motion(self, e):
        d = self.drag
        if not d:
            return
        x, y, gx, gy = self._pos(e)
        w = self.world
        d["moved"] = True
        if d["kind"] == "token":
            d["pos"] = (x, y)
        elif d["kind"] == "object":
            w.move_object(d["id"], round(gx - d["ox"]), round(gy - d["oy"]))
        else:
            w.move_room(d["id"], round(gx - d["ox"]), round(gy - d["oy"]))
        self.redraw()

    def on_release(self, e):
        d, self.drag = self.drag, None
        if not d:
            return
        x, y, gx, gy = self._pos(e)
        w = self.world
        if not d.get("moved"):
            self.redraw()
            return
        if d["kind"] == "token":
            if y < TOP:
                w.unplace(d["id"])
            else:
                room = w.room_at(gx, gy)
                if room:
                    w.set_position(d["id"], room["id"])
        elif d["kind"] == "object":
            w.drop_object(d["id"])
        self.commit()

    # ------------------------------------------------------------------ рисование

    def redraw(self):
        cv, w = self.cv, self.world
        cv.delete("all")
        width, height = GRID_W * CELL, TOP + GRID_H * CELL
        for gx in range(GRID_W + 1):
            cv.create_line(gx * CELL, TOP, gx * CELL, height, fill=GRID_LINE)
        for gy in range(GRID_H + 1):
            cv.create_line(0, TOP + gy * CELL, width, TOP + gy * CELL, fill=GRID_LINE)

        def box(i):
            return i["x"] * CELL, TOP + i["y"] * CELL, (i["x"] + i["w"]) * CELL, TOP + (i["y"] + i["h"]) * CELL

        for r in sorted(w.rooms, key=lambda r: -r["w"] * r["h"]):
            x0, y0, x1, y1 = box(r)
            on = self.sel == ("room", r["id"])
            cv.create_rectangle(x0, y0, x1, y1, fill=PANEL, outline=ACCENT if on else MUTED, width=3 if on else 1)
            cv.create_text(x0 + 8, y0 + 6, anchor="nw", text=safe(r["name"]), fill=FG, font=F.bold,
                           width=max(40, x1 - x0 - 12))
        for l in w.links:
            a, b = w.room_by_id(l["a"]), w.room_by_id(l["b"])
            if not a or not b:
                continue
            ra, rb = box(a), box(b)
            ca, cb = ((ra[0] + ra[2]) / 2, (ra[1] + ra[3]) / 2), ((rb[0] + rb[2]) / 2, (rb[1] + rb[3]) / 2)
            pa, pb = border_point(ra, cb), border_point(rb, ca)
            cv.create_line(pa[0], pa[1], pb[0], pb[1], fill=LINK_COLOR, width=2, dash=(6, 4))
            for p in (pa, pb):
                cv.create_oval(p[0] - 4, p[1] - 4, p[0] + 4, p[1] + 4, fill=ACCENT, outline="")
            if l.get("label"):
                cv.create_text((pa[0] + pb[0]) / 2, (pa[1] + pb[1]) / 2 - 8, text=safe(l["label"]),
                               fill=FG, font=F.small)
        for o in w.objects:
            x0, y0, x1, y1 = box(o)
            hidden = o.get("visibility", "all") != "all"
            on = self.sel == ("object", o["id"])
            cv.create_rectangle(x0 + 1, y0 + 1, x1 - 1, y1 - 1, fill=HIDDEN_FILL if hidden else OBJECT_FILL,
                                outline=ACCENT if on else MUTED, width=2 if on else 1,
                                dash=(4, 3) if hidden else ())
            cv.create_text(x0 + 4, y0 + 3, anchor="nw", text=safe(o["name"]), fill=FG, font=F.small,
                           width=max(20, x1 - x0 - 8))
            if o.get("state") and y1 - y0 >= CELL * 2:
                cv.create_text(x0 + 4, y1 - 4, anchor="sw", text=safe(o["state"]), fill=ACCENT, font=F.small,
                               width=max(20, x1 - x0 - 8))

        # фишки персонажей: в комнате — внизу, без места — в верхней полосе
        self.token_boxes = []
        names = list(self.s.order)
        slots = {}
        for n in names:
            room = w.where(n)
            key = room["id"] if room else None
            i = slots.get(key, 0)
            slots[key] = i + 1
            if room:
                x0, y0, x1, y1 = box(room)
                per_row = max(1, int((x1 - x0 - 8) // (TOKEN_R * 2 + 3)))
                cx = x0 + 6 + TOKEN_R + (i % per_row) * (TOKEN_R * 2 + 3)
                cy = y1 - 5 - TOKEN_R - (i // per_row) * (TOKEN_R * 2 + 3)
            else:
                cx, cy = 100 + TOKEN_R + i * (TOKEN_R * 2 + 4), TOP / 2
            self._token(n, cx, cy)
        cv.create_text(8, TOP / 2, anchor="w", text="Вне плана:", fill=MUTED, font=F.small)
        d = self.drag
        if d and d["kind"] == "token":
            self._token(d["id"], d["pos"][0], d["pos"][1], ghost=True)

    def _token(self, name, cx, cy, ghost=False):
        c = self.s.characters.get(name)
        color = c.color if c else MUTED
        self.cv.create_oval(cx - TOKEN_R, cy - TOKEN_R, cx + TOKEN_R, cy + TOKEN_R, fill=color,
                            outline=FG if ghost else BG, width=2)
        self.cv.create_text(cx, cy, text=safe(name[:1].upper()), fill=BG, font=F.bold)
        if not ghost:
            self.token_boxes.append((name, cx - TOKEN_R, cy - TOKEN_R, cx + TOKEN_R, cy + TOKEN_R))

    def close(self):
        self.app.plan_win = None
        self.top.destroy()
