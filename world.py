"""План бункера: комнаты, объекты, проходы и положение персонажей.

Чистая логика без интерфейса и без Ollama, поэтому её легко проверять тестами.
Координаты — в клетках сетки GRID_W × GRID_H."""
from __future__ import annotations

GRID_W, GRID_H = 36, 20   # размер плана в клетках
MIN_W, MIN_H = 4, 3       # минимальный размер комнаты (верхняя строка — для названия, нижняя — для фишек)

# Операции, которые сценарист может предложить для плана (автор подтверждает их списком).
OPS = ("add_room", "add_object", "remove_object", "remove_room", "set_state",
       "describe", "link", "unlink", "set_visibility", "move")


def norm(text):
    """Сравнение имён без учёта регистра, ё и лишних пробелов."""
    return " ".join(str(text or "").lower().replace("ё", "е").split())


def parse_visibility(text):
    """'' или «все» → 'all'; «автор» → 'author'; иначе список имён персонажей."""
    t = norm(text)
    if t in ("", "все", "всем", "all"):
        return "all"
    if t in ("автор", "только автор", "author"):
        return "author"
    return [x.strip() for x in str(text).replace(";", ",").split(",") if x.strip()]


def visibility_text(v):
    if v == "author":
        return "только автор"
    if isinstance(v, list):
        return ", ".join(v)
    return "все"


def _spot(bx0, by0, bx1, by1, w, h, rects, gap):
    """Первое свободное место w×h внутри границ; rects — занятые прямоугольники (x0, y0, x1, y1)."""
    for y in range(by0, by1 - h + 1):
        for x in range(bx0, bx1 - w + 1):
            if all(x + w + gap <= a or c + gap <= x or y + h + gap <= b or d + gap <= y
                   for a, b, c, d in rects):
                return x, y
    return None


def border_point(rect, target):
    """Точка на границе прямоугольника (x0, y0, x1, y1) в направлении от его центра к target."""
    x0, y0, x1, y1 = rect
    cx, cy = (x0 + x1) / 2, (y0 + y1) / 2
    dx, dy = target[0] - cx, target[1] - cy
    if dx == 0 and dy == 0:
        return cx, cy
    sx = (x1 - cx) / abs(dx) if dx else float("inf")
    sy = (y1 - cy) / abs(dy) if dy else float("inf")
    k = min(sx, sy)
    return cx + dx * k, cy + dy * k


class World:
    def __init__(self):
        self.rooms = []       # {id, name, description, x, y, w, h}
        self.objects = []     # {id, room, name, description, state, x, y, w, h, visibility}
        self.links = []       # {a, b, label}  — проход между комнатами (в обе стороны)
        self.positions = {}   # имя персонажа -> id комнаты
        self.synced_upto = 0  # до какого сообщения журнала сценарист уже обновил мир
        self.next_id = 1

    # ---------------------------------------------------------------- сохранение

    def to_dict(self):
        return {"rooms": self.rooms, "objects": self.objects, "links": self.links,
                "positions": self.positions, "synced_upto": self.synced_upto, "next_id": self.next_id}

    @classmethod
    def from_dict(cls, d):
        w = cls()
        w.rooms = list(d.get("rooms", []))
        w.objects = list(d.get("objects", []))
        w.links = list(d.get("links", []))
        w.positions = dict(d.get("positions", {}))
        w.synced_upto = int(d.get("synced_upto", 0))
        used = [int(x["id"][1:]) for x in w.rooms + w.objects if str(x.get("id", ""))[1:].isdigit()]
        w.next_id = max([int(d.get("next_id", 1))] + [i + 1 for i in used])
        return w

    def _new_id(self, prefix):
        i = f"{prefix}{self.next_id}"
        self.next_id += 1
        return i

    # ---------------------------------------------------------------- поиск

    def room_by_id(self, rid):
        return next((r for r in self.rooms if r["id"] == rid), None)

    def object_by_id(self, oid):
        return next((o for o in self.objects if o["id"] == oid), None)

    def get_room(self, ref):
        """Комната по id или названию (без учёта регистра и ё; допускается однозначное частичное совпадение)."""
        if not ref:
            return None
        r = self.room_by_id(ref)
        if r:
            return r
        n = norm(ref)
        exact = [r for r in self.rooms if norm(r["name"]) == n]
        if exact:
            return exact[0]
        part = [r for r in self.rooms if n in norm(r["name"]) or norm(r["name"]) in n]
        return part[0] if len(part) == 1 else None

    def objects_in(self, rid):
        return [o for o in self.objects if o["room"] == rid]

    def find_object(self, room_ref, name):
        """Объект по названию: сначала в указанной комнате, потом во всём плане."""
        n = norm(name)
        if not n:
            return None
        room = self.get_room(room_ref)
        pools = ([self.objects_in(room["id"])] if room else []) + [self.objects]
        for pool in pools:
            exact = [o for o in pool if norm(o["name"]) == n]
            if exact:
                return exact[0]
            part = [o for o in pool if n in norm(o["name"]) or norm(o["name"]) in n]
            if len(part) == 1:
                return part[0]
        return None

    def room_at(self, gx, gy):
        """Комната под точкой (в клетках); при наложении — самая маленькая."""
        hits = [r for r in self.rooms if r["x"] <= gx < r["x"] + r["w"] and r["y"] <= gy < r["y"] + r["h"]]
        return min(hits, key=lambda r: r["w"] * r["h"]) if hits else None

    def object_at(self, gx, gy):
        hits = [o for o in self.objects if o["x"] <= gx < o["x"] + o["w"] and o["y"] <= gy < o["y"] + o["h"]]
        return hits[-1] if hits else None

    @staticmethod
    def _rect(i):
        return i["x"], i["y"], i["x"] + i["w"], i["y"] + i["h"]

    @staticmethod
    def _inner(room):
        """Где в комнате можно ставить объекты (без верхней строки с названием и нижней — для фишек)."""
        return room["x"], room["y"] + 1, room["x"] + room["w"], room["y"] + room["h"] - 1

    # ---------------------------------------------------------------- комнаты

    def add_room(self, name, description="", w=8, h=5, x=None, y=None):
        name = " ".join(str(name or "").split())
        if not name:
            raise ValueError("Введите название комнаты.")
        if any(norm(r["name"]) == norm(name) for r in self.rooms):
            raise ValueError("Комната с таким названием уже есть.")
        w, h = max(MIN_W, min(GRID_W, int(w))), max(MIN_H, min(GRID_H, int(h)))
        if x is None or y is None:
            rects = [self._rect(r) for r in self.rooms]
            x, y = (_spot(0, 0, GRID_W, GRID_H, w, h, rects, 1)
                    or _spot(0, 0, GRID_W, GRID_H, w, h, rects, 0) or (0, 0))
        r = {"id": self._new_id("r"), "name": name, "description": str(description or "").strip(),
             "x": int(x), "y": int(y), "w": w, "h": h}
        self.rooms.append(r)
        return r

    def _shift(self, room, dx, dy):
        room["x"] += dx
        room["y"] += dy
        for o in self.objects_in(room["id"]):
            o["x"] += dx
            o["y"] += dy

    def move_room(self, rid, x, y):
        r = self.room_by_id(rid)
        x, y = max(0, min(GRID_W - r["w"], int(x))), max(0, min(GRID_H - r["h"], int(y)))
        self._shift(r, x - r["x"], y - r["y"])

    def resize_room(self, rid, w, h):
        r = self.room_by_id(rid)
        r["w"], r["h"] = max(MIN_W, min(GRID_W, int(w))), max(MIN_H, min(GRID_H, int(h)))
        self._shift(r, min(0, GRID_W - r["w"] - r["x"]), min(0, GRID_H - r["h"] - r["y"]))
        for o in self.objects_in(rid):
            self._clamp_obj(o)

    def delete_room(self, rid):
        self.rooms = [r for r in self.rooms if r["id"] != rid]
        self.objects = [o for o in self.objects if o["room"] != rid]
        self.links = [l for l in self.links if rid not in (l["a"], l["b"])]
        self.positions = {k: v for k, v in self.positions.items() if v != rid}

    # ---------------------------------------------------------------- объекты

    def add_object(self, room_ref, name, description="", state="", visibility="all", w=2, h=2):
        room = self.get_room(room_ref)
        if not room:
            raise ValueError("Сначала выберите комнату.")
        name = " ".join(str(name or "").split())
        if not name:
            raise ValueError("Введите название объекта.")
        if any(norm(o["name"]) == norm(name) for o in self.objects_in(room["id"])):
            raise ValueError("В этой комнате уже есть такой объект.")
        x0, y0, x1, y1 = self._inner(room)
        w, h = max(1, min(int(w), x1 - x0)), max(1, min(int(h), y1 - y0))
        spot = _spot(x0, y0, x1, y1, w, h, [self._rect(o) for o in self.objects_in(room["id"])], 0) or (x0, y0)
        o = {"id": self._new_id("o"), "room": room["id"], "name": name,
             "description": str(description or "").strip(), "state": str(state or "").strip(),
             "x": spot[0], "y": spot[1], "w": w, "h": h, "visibility": visibility}
        self.objects.append(o)
        return o

    def _clamp_obj(self, o):
        room = self.room_by_id(o["room"])
        if not room:
            return
        x0, y0, x1, y1 = self._inner(room)
        o["w"], o["h"] = max(1, min(o["w"], x1 - x0)), max(1, min(o["h"], y1 - y0))
        o["x"], o["y"] = max(x0, min(o["x"], x1 - o["w"])), max(y0, min(o["y"], y1 - o["h"]))

    def move_object(self, oid, x, y):
        """Свободное перетаскивание по плану; комнату определяет drop_object при отпускании."""
        o = self.object_by_id(oid)
        o["x"], o["y"] = max(0, min(GRID_W - o["w"], int(x))), max(0, min(GRID_H - o["h"], int(y)))

    def drop_object(self, oid):
        """Объект бросили: он принадлежит комнате, в которой оказался его центр, и остаётся внутри неё."""
        o = self.object_by_id(oid)
        r = self.room_at(o["x"] + o["w"] / 2, o["y"] + o["h"] / 2)
        if r and r["id"] != o["room"]:
            if not any(norm(x["name"]) == norm(o["name"]) for x in self.objects_in(r["id"])):
                o["room"] = r["id"]
        self._clamp_obj(o)

    def resize_object(self, oid, w, h):
        o = self.object_by_id(oid)
        o["w"], o["h"] = max(1, int(w)), max(1, int(h))
        self._clamp_obj(o)

    def delete_object(self, oid):
        self.objects = [o for o in self.objects if o["id"] != oid]

    @staticmethod
    def visible_to(o, who):
        v = o.get("visibility", "all")
        if v == "all":
            return True
        return isinstance(v, list) and who in v

    # ---------------------------------------------------------------- проходы

    def _link(self, a, b):
        return next((l for l in self.links if {l["a"], l["b"]} == {a, b}), None)

    def link(self, a_ref, b_ref, label=""):
        a, b = self.get_room(a_ref), self.get_room(b_ref)
        if not a or not b or a["id"] == b["id"]:
            raise ValueError("нужны две разные существующие комнаты")
        l = self._link(a["id"], b["id"])
        if l:
            l["label"] = str(label or "").strip() or l.get("label", "")
        else:
            self.links.append({"a": a["id"], "b": b["id"], "label": str(label or "").strip()})
        return a, b

    def unlink(self, a_ref, b_ref):
        a, b = self.get_room(a_ref), self.get_room(b_ref)
        l = self._link(a["id"], b["id"]) if a and b else None
        if l:
            self.links.remove(l)
        return bool(l)

    def neighbors(self, rid):
        out = []
        for l in self.links:
            other = l["b"] if l["a"] == rid else l["a"] if l["b"] == rid else None
            if other and self.room_by_id(other):
                out.append((self.room_by_id(other), l.get("label", "")))
        return out

    def reachable(self, a, b):
        """Можно ли дойти из комнаты a в b по проходам (через промежуточные комнаты тоже)."""
        seen, todo = {a}, [a]
        while todo:
            cur = todo.pop()
            if cur == b:
                return True
            for r, _ in self.neighbors(cur):
                if r["id"] not in seen:
                    seen.add(r["id"])
                    todo.append(r["id"])
        return a == b

    # ---------------------------------------------------------------- где кто

    def where(self, who):
        rid = self.positions.get(who)
        return self.room_by_id(rid) if rid else None

    def occupants(self, rid, order=None):
        names = [n for n, r in self.positions.items() if r == rid]
        return [n for n in order if n in names] if order else names

    def set_position(self, who, rid):
        self.positions[who] = rid

    def unplace(self, who):
        self.positions.pop(who, None)

    def rename_who(self, old, new):
        if old in self.positions:
            self.positions[new] = self.positions.pop(old)
        for o in self.objects:
            if isinstance(o.get("visibility"), list):
                o["visibility"] = [new if n == old else n for n in o["visibility"]]

    # ---------------------------------------------------------------- тексты для нейросетей

    def place_text(self, who):
        """Что видит и знает про окружение персонаж: комната, объекты (только видимые ему), соседи, выходы."""
        room = self.where(who)
        if not room:
            return ""
        lines = [f"Ты сейчас в комнате «{room['name']}»." + (f" {room['description']}" if room.get("description") else "")]
        objs = [o for o in self.objects_in(room["id"]) if self.visible_to(o, who)]
        if objs:
            lines.append("Здесь есть: " + "; ".join(
                o["name"] + (f" ({o['state']})" if o.get("state") else "")
                + (f" — {o['description']}" if o.get("description") else "") for o in objs) + ".")
        near = [n for n in self.occupants(room["id"]) if n != who]
        lines.append("Рядом с тобой: " + (", ".join(near) if near else "никого") + ".")
        exits = [r["name"] + (f" ({lab})" if lab else "") for r, lab in self.neighbors(room["id"])]
        if exits:
            lines.append("Выходы: " + "; ".join(exits) + ".")
        return "\n".join(lines)

    def map_text(self):
        """Карта бункера, которую знают все (только комнаты и проходы, без скрытых объектов)."""
        if len(self.rooms) < 2:
            return ""
        out = []
        for r in self.rooms:
            ex = [x["name"] + (f" ({lab})" if lab else "") for x, lab in self.neighbors(r["id"])]
            out.append(f"- {r['name']}: " + (", ".join(ex) if ex else "выходов нет"))
        return "\n".join(out)

    def full_text(self, order):
        """Полная картина для сценариста: все объекты (и скрытые), проходы, кто где."""
        if not self.rooms:
            return "(план пока пуст)"
        lines = []
        for r in self.rooms:
            lines.append(f"- {r['name']}" + (f": {r['description']}" if r.get("description") else ""))
            for o in self.objects_in(r["id"]):
                vis = o.get("visibility", "all")
                extra = "" if vis == "all" else f", видит: {visibility_text(vis)}"
                desc = f" — {o['description']}" if o.get("description") else ""
                lines.append(f"    · {o['name']} ({o.get('state') or 'в порядке'}{extra}){desc}")
            ex = self.neighbors(r["id"])
            if ex:
                lines.append("    проходы: " + "; ".join(x["name"] + (f" ({lab})" if lab else "") for x, lab in ex))
        if order:
            who = []
            for n in order:
                room = self.where(n)
                who.append(f"{n} — {room['name'] if room else 'не на плане'}")
            lines.append("ГДЕ КТО: " + "; ".join(who))
        return "\n".join(lines)

    # ---------------------------------------------------------------- правки от сценариста

    def op_text(self, op):
        k = op.get("op", "")
        room, obj, tgt, text = (str(op.get(x) or "") for x in ("room", "object", "target", "text"))
        tail = f" — {text}" if text else ""
        return {
            "add_room": f"Добавить комнату «{room}»{tail}",
            "add_object": f"В «{room}» добавить объект «{obj}»{tail}",
            "remove_object": f"Убрать объект «{obj}» из «{room}»",
            "remove_room": f"Удалить комнату «{room}» со всем содержимым",
            "set_state": f"«{obj}» ({room}): состояние → {text}",
            "describe": f"Описание «{obj}» ({room}) → {text}" if obj else f"Описание комнаты «{room}» → {text}",
            "link": f"Связать «{room}» и «{tgt}»" + (f" ({text})" if text else ""),
            "unlink": f"Убрать проход между «{room}» и «{tgt}»",
            "set_visibility": f"«{obj}» ({room}): видят → {text}",
            "move": f"Переместить {obj} в «{tgt}»",
        }.get(k, f"Неизвестная операция: {k}")

    def apply_ops(self, ops, order):
        """Применить операции по очереди. Возвращает (что сделано, что не удалось и почему)."""
        done, skipped = [], []
        for op in ops:
            try:
                done.append(self._apply_op(op, order))
            except ValueError as e:
                skipped.append(f"{self.op_text(op)} ({e})")
        return done, skipped

    def _apply_op(self, op, order):
        k = op.get("op")
        room, obj, tgt, text = (str(op.get(x) or "").strip() for x in ("room", "object", "target", "text"))
        if k == "add_room":
            return f"комната «{self.add_room(room, text)['name']}» добавлена"
        if k == "add_object":
            o = self.add_object(room, obj, text)
            return f"«{o['name']}» добавлен в «{self.room_by_id(o['room'])['name']}»"
        if k == "move":
            who = next((n for n in order if norm(n) == norm(obj)), None)
            r = self.get_room(tgt)
            if not who:
                raise ValueError(f"нет персонажа «{obj}»")
            if not r:
                raise ValueError(f"нет комнаты «{tgt}»")
            self.positions[who] = r["id"]
            return f"{who} теперь в «{r['name']}»"
        if k in ("link", "unlink"):
            if k == "unlink":
                if not self.unlink(room, tgt):
                    raise ValueError("такого прохода нет")
                return f"проход «{room}» — «{tgt}» убран"
            try:
                a, b = self.link(room, tgt, text)
            except ValueError as e:
                raise ValueError(f"{e}: «{room}», «{tgt}»") from None
            return f"«{a['name']}» и «{b['name']}» связаны"
        r = self.get_room(room)
        if not r:
            raise ValueError(f"нет комнаты «{room}»")
        if k == "remove_room":
            self.delete_room(r["id"])
            return f"комната «{r['name']}» удалена"
        if k == "describe" and not obj:
            r["description"] = text
            return f"описание «{r['name']}» обновлено"
        o = self.find_object(r["id"], obj)
        if k in ("remove_object", "set_state", "describe", "set_visibility") and not o:
            raise ValueError(f"нет объекта «{obj}»")
        if k == "remove_object":
            self.delete_object(o["id"])
            return f"«{o['name']}» убран"
        if k == "set_state":
            o["state"] = text
            return f"«{o['name']}»: {text}"
        if k == "describe":
            o["description"] = text
            return f"описание «{o['name']}» обновлено"
        if k == "set_visibility":
            o["visibility"] = parse_visibility(text)
            return f"«{o['name']}»: видят — {visibility_text(o['visibility'])}"
        raise ValueError("неизвестная операция")

    def apply_sync(self, res, order):
        """Автообновление от сценариста: перемещения и состояния. Возвращает (изменения, предупреждения)."""
        report, warns = [], []
        for mv in res.get("moves") or []:
            who, room = mv.get("who"), self.get_room(mv.get("room"))
            if who not in order or not room:
                continue
            prev = self.positions.get(who)
            if prev == room["id"]:
                continue
            self.positions[who] = room["id"]
            pr = self.room_by_id(prev) if prev else None
            report.append(f"{who}: {pr['name'] if pr else 'не было на плане'} → {room['name']}")
            if pr and not self.reachable(prev, room["id"]):
                warns.append(f"{who}: между «{pr['name']}» и «{room['name']}» нет прохода в плане")
        for st in res.get("states") or []:
            o = self.find_object(st.get("room"), st.get("object"))
            new = str(st.get("state") or "").strip()
            if o and new and new != o.get("state"):
                report.append(f"{o['name']}: {o.get('state') or 'в порядке'} → {new}")
                o["state"] = new
        self.synced_upto = int(res.get("upto", self.synced_upto))
        return report, warns
