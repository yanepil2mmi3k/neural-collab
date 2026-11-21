"""Хранение: сессия — это папка, у каждого персонажа в ней своя папка с листом и памятью.

sessions/<название>/
    session.json          настройки и порядок персонажей
    chat.json             общий журнал (сцены, задачи, реплики, скрытые мысли)
    world.json            план бункера: комнаты, объекты, проходы, кто где
    writer_chat.json      переписка автора со сценаристом
    characters/<имя>/
        sheet.json        лист персонажа и его модель
        memory.json       память и личные мысли
    exports/              готовые сценарии
sessions/_tasks.json      задачи автора для сценариста: общие для всех сессий, живут, пока их не закроют
"""
from __future__ import annotations

import json
import os
import re
import shutil
from datetime import datetime

from world import World

# (ключ, подпись, тип, высота поля). line — одна строка, text — абзац, lines — список по строкам.
# Чтобы добавить поле в лист, достаточно дописать сюда строку.
FIELDS = [
    ("age", "Возраст", "line", 1),
    ("height", "Рост", "line", 1),
    ("weight", "Вес", "line", 1),
    ("profession", "Профессия", "line", 1),
    ("appearance", "Внешность", "text", 2),
    ("personality", "Характер", "text", 3),
    ("speech", "Манера речи", "text", 2),
    ("hobbies", "Увлечения", "text", 2),
    ("goal", "Цели и мотивация", "text", 2),
    ("backstory", "Предыстория", "text", 4),
    ("relations", "Отношение к остальным", "text", 2),
    ("secrets", "Секреты", "text", 3),
    ("inventory", "Инвентарь", "lines", 3),
    ("state", "Состояние сейчас", "text", 2),
]
HINTS = {
    "secrets": "знает только он",
    "inventory": "по предмету в строке, нейросеть обновляет сама",
    "state": "здоровье, самочувствие, нейросеть обновляет сама",
}
PALETTE = ["#e0a84e", "#6db3d9", "#df7a72", "#86c48a", "#b99be0",
           "#e3d16f", "#62cdbd", "#ee8fb0", "#9fb0ea", "#e39a68"]

DEFAULT_SETTINGS = {
    "setting": "",            # общий сеттинг: где мы и что происходит
    "truth": "",              # правда автора: то, чего персонажи не знают (видит только сценарист)
    "director_model": "",
    "director_mode": "rules", # rules — правила без модели (быстро), model — модель-режиссёр
    "scribe_model": "",       # модель сценариста: мир, сюжет, итоги в память, экспорт сценария
    "director_note": "",
    "num_ctx": 8192,
    "temperature": 0.8,
    "history": 40,            # сколько последних сообщений видит персонаж
    "unload_others": False,   # Ollama сама освобождает память; явная выгрузка нужна редко
    "auto_sheet": True,       # нейросеть сама обновляет инвентарь и состояние
    "sync_every": 6,          # сценарист обновляет мир, когда накопилось столько новых сообщений (0 — вручную)
    "writer_sees_thoughts": True,
}


def safe_name(name):
    return re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", name).strip(" .") or "_"


def _read(path, default, problems=None):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except OSError:
        return default
    except ValueError as e:
        backup = path + ".broken"
        shutil.copy(path, backup)  # не затирать испорченный вручную файл
        if problems is not None:
            problems.append(f"{path}: {e}\nРезервная копия: {backup}")
        return default


def _write(path, data):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    os.replace(tmp, path)


class Character:
    def __init__(self, session_path, name, folder=None):
        self.name = name
        self.folder = folder or os.path.join(session_path, "characters", safe_name(name))
        self.model = ""
        self.color = PALETTE[0]
        self.sheet = {k: ([] if kind == "lines" else "") for k, _, kind, _ in FIELDS}
        self.memories = []        # постоянная память (итоги сцен, важные факты)
        self.thoughts = []        # недавние личные мысли
        self.summarized_upto = 0  # до какого сообщения журнала память уже подведена

    @classmethod
    def load(cls, session_path, folder, problems=None):
        d = _read(os.path.join(folder, "sheet.json"), {}, problems)
        c = cls(session_path, d.get("name") or os.path.basename(folder), folder)
        c.model = d.get("model", "")
        c.color = d.get("color", PALETTE[0])
        for k, _, kind, _ in FIELDS:
            v = d.get(k, c.sheet[k])
            if kind == "lines" and isinstance(v, str):
                v = [x.strip() for x in v.splitlines() if x.strip()]
            c.sheet[k] = v
        mem = _read(os.path.join(folder, "memory.json"), {}, problems)
        c.memories = list(mem.get("memories", []))
        c.thoughts = list(mem.get("thoughts", []))
        c.summarized_upto = int(mem.get("summarized_upto", 0))
        return c

    def save(self):
        _write(os.path.join(self.folder, "sheet.json"),
               {"name": self.name, "model": self.model, "color": self.color, **self.sheet})
        _write(os.path.join(self.folder, "memory.json"),
               {"memories": self.memories, "thoughts": self.thoughts,
                "summarized_upto": self.summarized_upto})


class Session:
    def __init__(self, path, tasks_path=None):
        self.path = path
        self.name = os.path.basename(path)
        self.load_problems = []
        meta = _read(os.path.join(path, "session.json"), {}, self.load_problems)
        self.settings = {**DEFAULT_SETTINGS, **meta.get("settings", {})}
        self.order = list(meta.get("order", []))
        self.log = _read(os.path.join(path, "chat.json"), [], self.load_problems)
        self.world = World.from_dict(_read(os.path.join(path, "world.json"), {}, self.load_problems))
        self.writer_log = _read(os.path.join(path, "writer_chat.json"), [], self.load_problems)
        tasks = tasks_path or os.path.join(os.path.dirname(os.path.abspath(path)), "_tasks.json")
        self.tasks = TaskList(tasks, self.load_problems)
        self.characters = {}
        cdir = os.path.join(path, "characters")
        if os.path.isdir(cdir):
            for d in sorted(os.listdir(cdir)):
                folder = os.path.join(cdir, d)
                if os.path.isfile(os.path.join(folder, "sheet.json")):
                    c = Character.load(path, folder, self.load_problems)
                    self.characters[c.name] = c
        self.order = ([n for n in self.order if n in self.characters]
                      + [n for n in self.characters if n not in self.order])

    @classmethod
    def create(cls, root, name):
        folder = os.path.join(root, safe_name(name))
        if os.path.exists(folder):
            raise ValueError("Сессия с таким названием уже существует.")
        os.makedirs(os.path.join(folder, "characters"))
        s = cls(folder)
        s.save_meta()
        s.save_log()
        s.save_world()
        return s

    # ---------- сохранение

    def save_meta(self):
        _write(os.path.join(self.path, "session.json"), {"settings": self.settings, "order": self.order})

    def save_log(self):
        _write(os.path.join(self.path, "chat.json"), self.log)

    def save_world(self):
        _write(os.path.join(self.path, "world.json"), self.world.to_dict())

    def add_writer_msg(self, role, text):
        """role: user (автор), ai (сценарист), note (служебная заметка, например итог обновления мира)."""
        m = {"role": role, "text": text, "ts": datetime.now().isoformat(timespec="seconds")}
        self.writer_log.append(m)
        _write(os.path.join(self.path, "writer_chat.json"), self.writer_log)
        return m

    # ---------- персонажи

    def add_character(self, name):
        name = name.strip()
        if not name:
            raise ValueError("Введите имя персонажа.")
        if name in self.characters:
            raise ValueError("Персонаж с таким именем уже есть.")
        c = Character(self.path, name)
        if os.path.exists(c.folder):
            raise ValueError("Папка с таким именем уже существует.")
        used = {x.color for x in self.characters.values()}
        c.color = next((p for p in PALETTE if p not in used), PALETTE[len(self.characters) % len(PALETTE)])
        self.characters[name] = c
        self.order.append(name)
        c.save()
        self.save_meta()
        return c

    def rename_character(self, old, new):
        new = new.strip()
        if new == old:
            return
        if not new or new in self.characters:
            raise ValueError("Имя пустое или уже занято.")
        c = self.characters[old]
        folder = os.path.join(self.path, "characters", safe_name(new))
        if folder != c.folder:
            if os.path.exists(folder):
                raise ValueError("Папка с таким именем уже существует.")
            os.rename(c.folder, folder)
            c.folder = folder
        c.name = new
        self.characters = {(new if k == old else k): v for k, v in self.characters.items()}
        self.order = [new if n == old else n for n in self.order]
        for m in self.log:
            if m.get("speaker") == old:
                m["speaker"] = new
            if m.get("audience"):
                m["audience"] = [new if n == old else n for n in m["audience"]]
        self.world.rename_who(old, new)
        c.save()
        self.save_meta()
        self.save_log()
        self.save_world()

    def delete_character(self, name):
        c = self.characters.pop(name)
        self.order.remove(name)
        shutil.rmtree(c.folder, ignore_errors=True)
        self.world.positions.pop(name, None)
        self.save_meta()
        self.save_world()

    # ---------- журнал

    def add_message(self, kind, text, speaker="", audience=None, thought=""):
        """kind: scene (сцена), task (задача), say (реплика персонажа).
        audience — список имён, которые видят сообщение; None — видят все."""
        m = {"id": (self.log[-1].get("id", len(self.log)) + 1) if self.log else 1,
             "ts": datetime.now().isoformat(timespec="seconds"),
             "kind": kind, "speaker": speaker, "text": text}
        if audience:
            m["audience"] = list(audience)
        if thought:
            m["thought"] = thought
        self.log.append(m)
        self.save_log()
        return m

    def pop_last(self):
        """Удалить последнее сообщение и откатить его следы в листе и мыслях персонажа."""
        if not self.log:
            return None
        m = self.log.pop()
        c = self.characters.get(m.get("speaker"))
        if c:
            if m.get("thought") and c.thoughts and c.thoughts[-1] == m["thought"]:
                c.thoughts.pop()
            d = m.get("delta")
            if d:
                inv = c.sheet.setdefault("inventory", [])
                for it in d.get("add", []):
                    if it in inv:
                        inv.remove(it)
                inv.extend(d.get("remove", []))
                if "state_before" in d:
                    c.sheet["state"] = d["state_before"]
        for x in self.characters.values():
            if x.summarized_upto > len(self.log):
                x.summarized_upto = len(self.log)
            x.save()
        if self.world.synced_upto > len(self.log):
            self.world.synced_upto = len(self.log)
            self.save_world()
        self.save_log()
        return m


class TaskList:
    """Задачи автора для сценариста («что я хочу увидеть»). Персонажам они неизвестны.
    Общие для всех сессий и остаются, пока автор их не закроет."""

    def __init__(self, path, problems=None):
        self.path = path
        d = _read(path, {}, problems)
        self.items = list(d.get("items", []))
        self.next_id = max([int(d.get("next_id", 1))] + [int(t["id"]) + 1 for t in self.items])

    def save(self):
        _write(self.path, {"items": self.items, "next_id": self.next_id})

    def get(self, tid):
        return next((t for t in self.items if t["id"] == tid), None)

    def add(self, text):
        text = " ".join(str(text or "").split())
        if not text:
            raise ValueError("Введите текст задачи.")
        t = {"id": self.next_id, "text": text, "done": False, "suggest_done": False,
             "created": datetime.now().isoformat(timespec="seconds")}
        self.next_id += 1
        self.items.append(t)
        self.save()
        return t

    def set_done(self, tid, done):
        t = self.get(tid)
        if t:
            t["done"], t["suggest_done"] = bool(done), False
            self.save()

    def remove(self, tid):
        self.items = [t for t in self.items if t["id"] != tid]
        self.save()

    def open_items(self):
        return [t for t in self.items if not t["done"]]

    def suggest_done(self, ids):
        """Сценарист считает, что задача выполнена; закрывает её только автор."""
        hit = [t for t in self.open_items() if t["id"] in set(ids)]
        for t in hit:
            t["suggest_done"] = True
        if hit:
            self.save()
        return hit


def list_sessions(root):
    """Названия сессий, самые свежие первыми."""
    out = []
    if os.path.isdir(root):
        for d in os.listdir(root):
            files = [os.path.join(root, d, f) for f in ("session.json", "chat.json")]
            if os.path.isfile(files[0]):
                out.append((max(os.path.getmtime(f) for f in files if os.path.isfile(f)), d))
    return [d for _, d in sorted(out, reverse=True)]
