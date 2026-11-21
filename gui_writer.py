"""Правая колонка: панель сценариста и задачи автора, плюс диалоги с его предложениями."""
from __future__ import annotations

import tkinter as tk
from tkinter import messagebox, ttk

from ui import ACCENT, BG, FG, FIELD, MUTED, THOUGHT, F, mk_text, modal, safe


class WriterPanel:
    """Переписка с сценаристом: вопросы, варианты сюжета, правка плана, проверка и обновление мира."""

    def __init__(self, app, parent):
        self.app = app
        self.frame = f = ttk.Frame(parent, padding=8)
        self.live = tk.StringVar(value="")

        bar = ttk.Frame(f)
        bar.pack(fill="x")
        for i, (text, cmd, guard) in enumerate((("Следующий шаг", app.writer_next_step, True),
                                                 ("Проверить мир", app.writer_check, True),
                                                 ("Обновить мир", app.world_sync_now, True),
                                                 ("План", app.open_plan, False))):
            bar.columnconfigure(i, weight=1, uniform="w")
            app.btn(bar, text, cmd, guard=guard).grid(row=0, column=i, sticky="ew", padx=(0 if i == 0 else 3, 0))

        wrap = ttk.Frame(f)
        wrap.pack(fill="both", expand=True, pady=(8, 0))
        wrap.rowconfigure(0, weight=1)
        wrap.columnconfigure(0, weight=1)
        self.chat = c = tk.Text(wrap, wrap="word", state="disabled", width=44, height=20, bg=FIELD, fg=FG,
                                relief="flat", highlightthickness=0, padx=12, pady=10, font=F.base,
                                cursor="arrow", selectbackground="#4a5575")
        sb = ttk.Scrollbar(wrap, command=c.yview)
        c.config(yscrollcommand=sb.set)
        c.grid(row=0, column=0, sticky="nsew")
        sb.grid(row=0, column=1, sticky="ns")
        c.tag_configure("who_me", font=F.bold, foreground=ACCENT, spacing1=10)
        c.tag_configure("who_ai", font=F.bold, foreground=THOUGHT, spacing1=10)
        c.tag_configure("text", font=F.base, lmargin1=6, lmargin2=6)
        c.tag_configure("note", font=F.small, foreground=MUTED, lmargin1=6, lmargin2=6, spacing1=8)

        ttk.Label(f, textvariable=self.live, style="Live.TLabel", wraplength=380, justify="left").pack(
            fill="x", pady=(6, 0))
        self.inp = mk_text(f, 3)
        self.inp.pack(fill="x", pady=(6, 0))
        self.inp.bind("<Control-Return>", lambda e: (app.writer_ask(), "break")[1])
        row = ttk.Frame(f)
        row.pack(fill="x", pady=(6, 0))
        app.btn(row, "Спросить  (Ctrl+Enter)", app.writer_ask, "Accent.TButton").pack(side="left", fill="x", expand=True)
        app.btn(row, "Изменить план", app.writer_edit_plan).pack(side="left", padx=(6, 0))
        ttk.Label(f, style="Muted.TLabel", wraplength=380, justify="left",
                  text="Сценарист видит тайные сообщения, мысли и скрытые объекты. "
                       "«Следующий шаг» учитывает текст в поле как пожелание.").pack(anchor="w", pady=(8, 0))

    def take_input(self):
        text = self.inp.get("1.0", "end").strip()
        self.inp.delete("1.0", "end")
        return text

    def render_all(self):
        c = self.chat
        c.config(state="normal")
        c.delete("1.0", "end")
        s = self.app.session
        for m in (s.writer_log if s else []):
            if m["role"] == "note":
                c.insert("end", safe(m["text"]) + "\n", ("note",))
            else:
                me = m["role"] == "user"
                c.insert("end", ("Вы" if me else "Сценарист") + "\n", ("who_me" if me else "who_ai",))
                c.insert("end", safe(m["text"]) + "\n", ("text",))
        c.config(state="disabled")
        c.see("end")


class TasksPanel:
    """Задачи автора: «что я хочу увидеть». Их знает только сценарист; живут, пока автор их не закроет."""

    def __init__(self, app, parent):
        self.app = app
        self.ids = []
        self.frame = f = ttk.Frame(parent, padding=8)
        ttk.Label(f, style="Muted.TLabel", wraplength=390, justify="left",
                  text="Что вы хотите увидеть. Персонажам эти задачи неизвестны, сценарист учитывает их "
                       "в предложениях и сам отмечает, когда задача, похоже, выполнена. Закрываете её вы.").pack(anchor="w")
        wrap = ttk.Frame(f)
        wrap.pack(fill="both", expand=True, pady=(8, 0))
        wrap.rowconfigure(0, weight=1)
        wrap.columnconfigure(0, weight=1)
        self.lb = tk.Listbox(wrap, height=14, exportselection=False, activestyle="none", bg=FIELD, fg=FG,
                             selectbackground=ACCENT, selectforeground=BG, relief="flat", highlightthickness=0,
                             font=F.base)
        sb = ttk.Scrollbar(wrap, command=self.lb.yview)
        self.lb.config(yscrollcommand=sb.set)
        self.lb.grid(row=0, column=0, sticky="nsew")
        sb.grid(row=0, column=1, sticky="ns")
        row = ttk.Frame(f)
        row.pack(fill="x", pady=(8, 0))
        ttk.Button(row, text="Выполнена / вернуть", command=self.toggle).pack(side="left")
        ttk.Button(row, text="Удалить", command=self.remove).pack(side="left", padx=6)
        app.btn(row, "Сцена по задаче", self.scene_for).pack(side="left")
        self.inp = mk_text(f, 2)
        self.inp.pack(fill="x", pady=(10, 0))
        self.inp.bind("<Control-Return>", lambda e: (self.add(), "break")[1])
        ttk.Button(f, text="Добавить задачу  (Ctrl+Enter)", style="Accent.TButton", command=self.add).pack(
            fill="x", pady=(6, 0))
        ttk.Label(f, style="Muted.TLabel", text="○ открыта   ◉ похоже, выполнена   ✓ закрыта").pack(
            anchor="w", pady=(8, 0))

    def refresh(self):
        s = self.app.session
        self.lb.delete(0, "end")
        self.ids = []
        if not s:
            return
        for t in sorted(s.tasks.items, key=lambda t: (t["done"], t["id"])):
            mark = "✓" if t["done"] else ("◉" if t.get("suggest_done") else "○")
            self.lb.insert("end", safe(f" {mark} #{t['id']}  {t['text']}"))
            if t["done"]:
                self.lb.itemconfig(len(self.ids), foreground=MUTED)
            self.ids.append(t["id"])

    def _selected(self):
        sel = self.lb.curselection()
        return self.ids[sel[0]] if sel and sel[0] < len(self.ids) else None

    def add(self):
        if not self.app.ready():
            return
        try:
            self.app.session.tasks.add(self.inp.get("1.0", "end"))
        except ValueError as e:
            messagebox.showinfo("Задача", str(e))
            return
        self.inp.delete("1.0", "end")
        self.refresh()

    def toggle(self):
        tid = self._selected()
        if tid is not None:
            t = self.app.session.tasks.get(tid)
            self.app.session.tasks.set_done(tid, not t["done"])
            self.refresh()

    def remove(self):
        tid = self._selected()
        if tid is not None and messagebox.askyesno("Удалить задачу", "Удалить выбранную задачу?"):
            self.app.session.tasks.remove(tid)
            self.refresh()

    def scene_for(self):
        tid = self._selected()
        if tid is None:
            messagebox.showinfo("Задача", "Выберите задачу в списке.")
            return
        t = self.app.session.tasks.get(tid)
        self.app.writer_next_step(task_id=tid, hint=f"Нужна сцена для задачи #{tid}: {t['text']}")


class OptionsDialog:
    """Варианты следующего шага. Окно не блокирует главное: текст вставляется в поле ввода, отправляете вы."""

    def __init__(self, app, options):
        self.app, self.options, self.cur = app, options, None
        self.top = top = modal(app, "Варианты следующего шага", 920, 620, grab=False)
        left = ttk.Frame(top, padding=(14, 14, 6, 14))
        left.pack(side="left", fill="y")
        ttk.Label(left, text="Варианты", style="Head.TLabel").pack(anchor="w")
        self.lb = tk.Listbox(left, width=30, exportselection=False, activestyle="none", bg=FIELD, fg=FG,
                             selectbackground=ACCENT, selectforeground=BG, relief="flat", highlightthickness=0,
                             font=F.bold)
        self.lb.pack(fill="y", expand=True, pady=(6, 0))
        for o in options:
            self.lb.insert("end", safe(" " + o["title"]))
        self.lb.bind("<<ListboxSelect>>", lambda e: self.show())

        right = ttk.Frame(top, padding=(6, 14, 14, 14))
        right.pack(side="left", fill="both", expand=True)
        self.task_lbl = ttk.Label(right, text="", style="Muted.TLabel")
        self.task_lbl.pack(anchor="w")
        ttk.Label(right, text="Сцена для всех (можно править)", style="Muted.TLabel").pack(anchor="w", pady=(6, 2))
        self.scene = mk_text(right, 8)
        self.scene.pack(fill="x")
        ttk.Button(right, text="Вставить как сцену", style="Accent.TButton", command=self.use_scene).pack(
            anchor="w", pady=(8, 0))
        ttk.Label(right, text="Тайное задание — кому (имена через запятую)", style="Muted.TLabel").pack(
            anchor="w", pady=(16, 2))
        self.to = ttk.Entry(right)
        self.to.pack(fill="x")
        ttk.Label(right, text="Текст тайного задания (можно править)", style="Muted.TLabel").pack(anchor="w", pady=(8, 2))
        self.secret = mk_text(right, 5)
        self.secret.pack(fill="x")
        ttk.Button(right, text="Вставить как тайное", command=self.use_secret).pack(anchor="w", pady=(8, 0))
        ttk.Label(right, style="Muted.TLabel", justify="left", wraplength=560,
                  text="Текст попадёт в поле ввода главного окна, проверьте его и нажмите «Отправить». "
                       "Окно можно не закрывать: вставьте сцену, отправьте её, затем вставьте тайное.").pack(
            anchor="w", pady=(16, 0))
        ttk.Button(right, text="Закрыть", command=top.destroy).pack(anchor="e", side="bottom")
        self.lb.selection_set(0)
        self.show()

    def _store(self):
        if self.cur is not None:
            o = self.options[self.cur]
            o["scene"] = self.scene.get("1.0", "end").strip()
            o["secret"] = self.secret.get("1.0", "end").strip()
            o["secret_to"] = [x.strip() for x in self.to.get().replace(";", ",").split(",") if x.strip()]

    def show(self):
        sel = self.lb.curselection()
        if not sel:
            return
        self._store()
        self.cur = sel[0]
        o = self.options[self.cur]
        self.scene.delete("1.0", "end")
        self.scene.insert("1.0", o["scene"])
        self.secret.delete("1.0", "end")
        self.secret.insert("1.0", o["secret"])
        self.to.delete(0, "end")
        self.to.insert(0, ", ".join(o["secret_to"]))
        t = self.app.session.tasks.get(o.get("task") or 0)
        self.task_lbl.config(text=safe(f"Помогает задаче #{t['id']}: {t['text']}") if t else "")

    def use_scene(self):
        self._store()
        self.app.compose("scene", self.options[self.cur]["scene"], None)

    def use_secret(self):
        self._store()
        o = self.options[self.cur]
        known = [n for n in o["secret_to"] if n in self.app.session.characters]
        if not o["secret"] or not known:
            messagebox.showinfo("Тайное задание", "Укажите, кому адресовано задание (имена из списка "
                                "персонажей), и текст.", parent=self.top)
            return
        self.app.compose("task", o["secret"], known)


class OpsDialog:
    """Предлагаемые правки плана списком: автор выбирает, что применить."""

    def __init__(self, app, summary, ops):
        self.app, self.ops = app, ops
        self.top = top = modal(app, "Правки плана от сценариста", 760, 520)
        ttk.Label(top, text=safe(summary) or "Сценарист предлагает изменения плана", style="Head.TLabel",
                  wraplength=700, justify="left").pack(anchor="w", padx=14, pady=(14, 4))
        ttk.Label(top, text="Выделите строки, которые нужно применить (по умолчанию выделено всё).",
                  style="Muted.TLabel").pack(anchor="w", padx=14)
        self.lb = tk.Listbox(top, selectmode="extended", exportselection=False, activestyle="none", bg=FIELD, fg=FG,
                             selectbackground=ACCENT, selectforeground=BG, relief="flat", highlightthickness=0,
                             font=F.base)
        self.lb.pack(fill="both", expand=True, padx=14, pady=10)
        for op in ops:
            self.lb.insert("end", safe(" " + app.session.world.op_text(op)))
        self.lb.selection_set(0, "end")
        bar = ttk.Frame(top)
        bar.pack(fill="x", padx=14, pady=(0, 14))
        ttk.Button(bar, text="Применить выбранное", style="Accent.TButton", command=self.apply).pack(side="right")
        ttk.Button(bar, text="Отмена", command=top.destroy).pack(side="right", padx=8)

    def apply(self):
        chosen = [self.ops[i] for i in self.lb.curselection()]
        self.top.destroy()
        if chosen:
            self.app.apply_plan_ops(chosen)
