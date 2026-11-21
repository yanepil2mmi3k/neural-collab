"""Диалоги: лист персонажа, настройки сессии, итоги сцены → память, экспорт сценария."""
from __future__ import annotations

import os
import tkinter as tk
from tkinter import messagebox, ttk

from storage import FIELDS, HINTS
from ui import BG, F, lines_of, mk_text, modal, safe


class CharDialog:
    """Лист персонажа (форма), его постоянная память и личные мысли."""

    def __init__(self, app, name=None):
        self.app, self.s = app, app.session
        self.c = self.s.characters.get(name) if name else None
        self.top = top = modal(app, f"Персонаж — {name}" if self.c else "Новый персонаж", 1040, 700)
        nb = ttk.Notebook(top)
        nb.pack(fill="both", expand=True, padx=12, pady=(12, 0))
        sheet, mem, thoughts = (ttk.Frame(nb, padding=10) for _ in range(3))
        nb.add(sheet, text="Лист")
        nb.add(mem, text="Память")
        nb.add(thoughts, text="Мысли")

        self.w = {}
        self.name_var = tk.StringVar(value=self.c.name if self.c else "")
        self.model_var = tk.StringVar(value=self.c.model if self.c else "")
        head = ttk.Frame(sheet)
        head.pack(fill="x")
        for col in range(3):
            head.columnconfigure(col, weight=1, uniform="h")

        def cell(i, label):
            f = ttk.Frame(head)
            f.grid(row=i // 3, column=i % 3, sticky="ew", padx=4, pady=3)
            ttk.Label(f, text=label, style="Muted.TLabel").pack(anchor="w")
            return f

        ttk.Entry(cell(0, "Имя"), textvariable=self.name_var).pack(fill="x")
        ttk.Combobox(cell(1, "Модель Ollama (у каждого своя)"), textvariable=self.model_var,
                     values=app.models).pack(fill="x")
        i = 2
        for key, label, kind, _h in FIELDS:
            if kind == "line":
                e = ttk.Entry(cell(i, label))
                e.pack(fill="x")
                self.w[key] = e
                i += 1

        cols = ttk.Frame(sheet)
        cols.pack(fill="both", expand=True, pady=(6, 0))
        halves = (ttk.Frame(cols), ttk.Frame(cols))
        for n, f in enumerate(halves):
            f.grid(row=0, column=n, sticky="new")
            cols.columnconfigure(n, weight=1, uniform="c")
        blocks = [f for f in FIELDS if f[2] != "line"]
        for n, (key, label, kind, h) in enumerate(blocks):
            f = ttk.Frame(halves[0] if n < (len(blocks) + 1) // 2 else halves[1])
            f.pack(fill="x", padx=4, pady=3)
            ttk.Label(f, text=label + (f" — {HINTS[key]}" if key in HINTS else ""),
                      style="Muted.TLabel").pack(anchor="w")
            t = mk_text(f, h)
            t.pack(fill="x")
            self.w[key] = t

        ttk.Label(mem, style="Muted.TLabel", justify="left", wraplength=940,
                  text="Постоянная память: одна запись в строке. Персонаж видит её целиком при каждой реплике. "
                       "Сюда попадают принятые итоги сцен; можно править и дописывать своё.").pack(anchor="w")
        self.mem = mk_text(mem, 20)
        self.mem.pack(fill="both", expand=True, pady=6)
        ttk.Label(thoughts, style="Muted.TLabel", justify="left", wraplength=940,
                  text="Недавние личные мысли: одна в строке. В запрос попадают последние восемь, так что "
                       "здесь удобно подбросить подозрение или идею. Постоянное лучше писать в «Память».").pack(anchor="w")
        self.th = mk_text(thoughts, 20)
        self.th.pack(fill="both", expand=True, pady=6)

        if self.c:
            for key, _label, kind, _h in FIELDS:
                v = self.c.sheet.get(key, "")
                if kind == "line":
                    self.w[key].insert(0, str(v))
                else:
                    self.w[key].insert("1.0", "\n".join(v) if isinstance(v, list) else str(v))
            self.mem.insert("1.0", "\n".join(self.c.memories))
            self.th.insert("1.0", "\n".join(self.c.thoughts))

        bar = ttk.Frame(top)
        bar.pack(fill="x", padx=12, pady=12)
        ttk.Button(bar, text="Сохранить", style="Accent.TButton", command=self.save).pack(side="right")
        ttk.Button(bar, text="Отмена", command=top.destroy).pack(side="right", padx=8)

    def save(self):
        s, c = self.s, self.c
        name = self.name_var.get().strip()
        try:
            if c is None:
                c = s.add_character(name)
            elif name != c.name:
                s.rename_character(c.name, name)
        except (ValueError, OSError) as e:
            messagebox.showerror("Персонаж", str(e), parent=self.top)
            return
        c.model = self.model_var.get().strip()
        for key, _label, kind, _h in FIELDS:
            w = self.w[key]
            v = w.get().strip() if kind == "line" else w.get("1.0", "end").strip()
            c.sheet[key] = [x.strip() for x in v.splitlines() if x.strip()] if kind == "lines" else v
        c.memories, c.thoughts = lines_of(self.mem), lines_of(self.th)
        c.save()
        self.top.destroy()
        self.app.refresh_roster()
        self.app.render_all()
        self.app.world_changed()


class SettingsDialog:
    MODES = {"rules": "Правила (быстро, без модели)", "model": "Модель-режиссёр"}

    def __init__(self, app):
        self.app, self.s = app, app.session
        st = self.s.settings
        self.top = top = modal(app, "Настройки сессии", 840, 720)
        nb = ttk.Notebook(top)
        nb.pack(fill="both", expand=True, padx=12, pady=(12, 0))
        a, b = ttk.Frame(nb, padding=14), ttk.Frame(nb, padding=14)
        nb.add(a, text="Мир и сюжет")
        nb.add(b, text="Модели и скорость")

        def block(parent, title, height, value):
            ttk.Label(parent, text=title, style="Muted.TLabel", justify="left", wraplength=760).pack(
                anchor="w", pady=(10, 2))
            t = mk_text(parent, height)
            t.pack(fill="x")
            t.insert("1.0", value)
            return t

        self.setting = block(a, "Мир и ситуация: общий сеттинг, его знают все персонажи", 7, st["setting"])
        self.truth = block(a, "Правда автора: то, чего персонажи не знают (кто предатель, что сломано на самом деле). "
                              "Видит только сценарист", 6, st["truth"])
        self.note = block(a, "Указание режиссёру (необязательно), например: «чаще давай слово тому, кого обвиняют»",
                          3, st["director_note"])

        self.writer_m, self.dir_m = tk.StringVar(value=st["scribe_model"]), tk.StringVar(value=st["director_model"])
        self.mode = tk.StringVar(value=self.MODES.get(st.get("director_mode"), self.MODES["rules"]))
        row = ttk.Frame(b)
        row.pack(fill="x")
        for i, (label, var, values) in enumerate((
                ("Модель сценариста (мир, сюжет, итоги, экспорт)", self.writer_m, app.models),
                ("Модель режиссёра (нужна в режиме «Модель-режиссёр»)", self.dir_m, app.models),
                ("Как режиссёр выбирает, кто говорит", self.mode, list(self.MODES.values())))):
            f = ttk.Frame(row)
            f.grid(row=0, column=i, sticky="ew", padx=(0 if i == 0 else 10, 0))
            row.columnconfigure(i, weight=1, uniform="m")
            ttk.Label(f, text=label, style="Muted.TLabel", wraplength=240, justify="left").pack(anchor="w")
            ttk.Combobox(f, textvariable=var, values=values, state="readonly" if i == 2 else "normal").pack(fill="x")
        ttk.Label(b, style="Muted.TLabel", justify="left", wraplength=760,
                  text="Режим «Правила» не вызывает модель: слово получает тот, к кому обратились по имени, затем "
                       "те, кто в одной комнате с говорившим, затем тот, кто дольше молчит. Он не тратит время на "
                       "смену моделей. Сценарист может быть той же моделью, что и один из персонажей.").pack(
            anchor="w", pady=(10, 0))

        nums = ttk.Frame(b)
        nums.pack(fill="x", pady=(16, 0))
        self.num = {}
        for i, (key, label, lo, hi, inc, fmt) in enumerate((
                ("num_ctx", "Окно контекста, токенов", 2048, 32768, 1024, "%.0f"),
                ("temperature", "Температура", 0.1, 1.5, 0.1, "%.1f"),
                ("history", "Сообщений в памяти персонажа", 5, 300, 5, "%.0f"),
                ("sync_every", "Обновлять мир каждые N сообщений (0 — вручную)", 0, 100, 1, "%.0f"))):
            f = ttk.Frame(nums)
            f.grid(row=0, column=i, sticky="nw", padx=(0, 18))
            ttk.Label(f, text=label, style="Muted.TLabel", wraplength=170, justify="left").pack(anchor="w")
            v = tk.StringVar(value=str(st[key]))
            ttk.Spinbox(f, from_=lo, to=hi, increment=inc, format=fmt, textvariable=v, width=9).pack(anchor="w")
            self.num[key] = v

        self.unload = tk.BooleanVar(value=st["unload_others"])
        self.auto = tk.BooleanVar(value=st["auto_sheet"])
        self.thoughts = tk.BooleanVar(value=st["writer_sees_thoughts"])
        for var, text in ((self.thoughts, "Сценарист видит мысли персонажей"),
                          (self.auto, "После реплики нейросеть сама обновляет инвентарь и состояние в листе"),
                          (self.unload, "Выгружать остальные модели из памяти перед сменой (обычно не нужно: "
                                        "Ollama сама освобождает память)")):
            ttk.Checkbutton(b, variable=var, text=text).pack(anchor="w", pady=(10, 0))

        bar = ttk.Frame(top)
        bar.pack(fill="x", padx=12, pady=12)
        ttk.Button(bar, text="Сохранить", style="Accent.TButton", command=self.save).pack(side="right")
        ttk.Button(bar, text="Отмена", command=top.destroy).pack(side="right", padx=8)

    def save(self):
        try:
            nums = {"num_ctx": int(float(self.num["num_ctx"].get())),
                    "temperature": float(self.num["temperature"].get().replace(",", ".")),
                    "history": int(float(self.num["history"].get())),
                    "sync_every": int(float(self.num["sync_every"].get()))}
        except ValueError:
            messagebox.showerror("Настройки", "Числовые поля заполнены неверно.", parent=self.top)
            return
        mode = next((k for k, v in self.MODES.items() if v == self.mode.get()), "rules")
        st = self.s.settings
        st.update(nums)
        st.update(setting=self.setting.get("1.0", "end").strip(), truth=self.truth.get("1.0", "end").strip(),
                  director_note=self.note.get("1.0", "end").strip(), director_mode=mode,
                  scribe_model=self.writer_m.get().strip(), director_model=self.dir_m.get().strip(),
                  unload_others=bool(self.unload.get()), auto_sheet=bool(self.auto.get()),
                  writer_sees_thoughts=bool(self.thoughts.get()))
        self.s.save_meta()
        self.top.destroy()
        self.app.refresh_queue()


class SummaryDialog:
    """Нейросеть предлагает записи в память — пользователь правит и принимает."""

    def __init__(self, app, proposals):
        self.app = app
        self.top = top = modal(app, "Итоги сцены → память персонажей", 840, 580)
        ttk.Label(top, style="Muted.TLabel", justify="left",
                  text="Сценарист предлагает записи в память. Поправьте текст и отметьте, "
                       "что принять: непринятое останется для следующих итогов.").pack(anchor="w", padx=14, pady=(14, 6))
        nb = ttk.Notebook(top)
        nb.pack(fill="both", expand=True, padx=14)
        self.items = {}
        for name, lines in proposals.items():
            f = ttk.Frame(nb, padding=10)
            nb.add(f, text=name)
            var = tk.BooleanVar(value=True)
            ttk.Checkbutton(f, text="Принять эти записи в память", variable=var).pack(anchor="w")
            t = mk_text(f, 12)
            t.pack(fill="both", expand=True, pady=8)
            t.insert("1.0", "\n".join(lines))
            self.items[name] = (var, t)
        bar = ttk.Frame(top)
        bar.pack(fill="x", padx=14, pady=14)
        ttk.Button(bar, text="Сохранить принятое", style="Accent.TButton", command=self.accept).pack(side="right")
        ttk.Button(bar, text="Закрыть", command=top.destroy).pack(side="right", padx=8)

    def accept(self):
        s, saved = self.app.session, 0
        for name, (var, txt) in self.items.items():
            if var.get() and name in s.characters:
                c = s.characters[name]
                c.memories.extend(lines_of(txt))
                c.summarized_upto = len(s.log)
                c.save()
                saved += 1
        self.top.destroy()
        self.app.status.set(f"Память обновлена у персонажей: {saved}")


class ExportDialog:
    def __init__(self, app):
        self.app = app
        st = app.session.settings
        self.top = top = modal(app, "Экспорт сценария", 580, 330)
        ttk.Label(top, style="Muted.TLabel", justify="left",
                  text="Нейросеть оформит журнал сессии как сценарий: сцены, ремарки, реплики.\n"
                       "Файл сохранится в папку exports внутри сессии.").pack(anchor="w", padx=14, pady=(14, 10))
        ttk.Label(top, text="Модель-сценарист", style="Muted.TLabel").pack(anchor="w", padx=14)
        self.model = tk.StringVar(value=st["scribe_model"] or st["director_model"])
        ttk.Combobox(top, textvariable=self.model, values=app.models).pack(fill="x", padx=14)
        self.th = tk.BooleanVar(value=False)
        ttk.Checkbutton(top, variable=self.th, text="Добавить мысли персонажей как закадровый голос").pack(
            anchor="w", padx=14, pady=(14, 0))
        bar = ttk.Frame(top)
        bar.pack(side="bottom", fill="x", padx=14, pady=14)
        ttk.Button(bar, text="Создать сценарий", style="Accent.TButton", command=self.go).pack(side="right")
        ttk.Button(bar, text="Отмена", command=top.destroy).pack(side="right", padx=8)

    def go(self):
        model, thoughts = self.model.get().strip(), bool(self.th.get())
        self.top.destroy()
        self.app.export_script(model, thoughts)


class ScriptWindow:
    def __init__(self, app, path, text):
        top = tk.Toplevel(app.root)
        top.title("Сценарий — " + os.path.basename(path))
        top.configure(bg=BG)
        top.geometry("860x700")
        t = mk_text(top, 30)
        t.configure(font=F.chat)
        t.pack(fill="both", expand=True, padx=12, pady=(12, 6))
        t.insert("1.0", safe(text))
        bar = ttk.Frame(top)
        bar.pack(fill="x", padx=12, pady=(0, 12))
        ttk.Label(bar, text="Сохранено: " + path, style="Muted.TLabel").pack(side="left")

        def copy():
            top.clipboard_clear()
            top.clipboard_append(t.get("1.0", "end"))

        ttk.Button(bar, text="Копировать всё", command=copy).pack(side="right")
