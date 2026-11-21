"""Главное окно: журнал, очередь, персонажи, панель сценариста, задачи и план бункера."""
from __future__ import annotations

import os
import queue
import subprocess
import sys
import threading
import time
import traceback
import tkinter as tk
from tkinter import messagebox, simpledialog, ttk

from engine import Engine, EngineError, last_speaker, next_in_order, rules_pick, writer_model
from gui_dialogs import CharDialog, ExportDialog, ScriptWindow, SettingsDialog, SummaryDialog
from gui_plan import PlanWindow
from gui_writer import OpsDialog, OptionsDialog, TasksPanel, WriterPanel
from ollama_client import Ollama, OllamaError
from storage import Session, list_sessions
from ui import ACCENT, BG, FG, FIELD, MUTED, PANEL, THOUGHT, F, mk_text, modal, safe


class QueueStrip:
    """Полоса очереди: закрашен тот, кто говорит сейчас; рамка из ▶ — кого предлагают правила режиссёра;
    белая рамка — кто говорил последним."""
    ROW = 28

    def __init__(self, parent):
        self.cv = tk.Canvas(parent, height=self.ROW + 4, bg=BG, highlightthickness=0)
        self.state = ([], None, None, None)
        self.cv.bind("<Configure>", lambda e: self.draw())

    def update(self, items, last=None, nxt=None, active=None):
        self.state = (items, last, nxt, active)
        self.draw()

    def draw(self):
        cv = self.cv
        items, last, nxt, active = self.state
        cv.delete("all")
        width = max(int(cv.winfo_width()), 240)
        x, y = 4, 4
        for name, color in items:
            label = name if len(name) <= 14 else name[:13] + "…"
            if name == nxt and name != active:
                label = "▶ " + label
            pw = int(F.small.measure(label)) + 36
            if x + pw > width - 4 and x > 4:
                x, y = 4, y + self.ROW
            on = name == active
            ring = ACCENT if name == nxt else (FG if name == last else PANEL)
            cv.create_rectangle(x, y, x + pw, y + 22, fill=ACCENT if on else PANEL, outline=ring,
                                width=2 if ring != PANEL else 1)
            cv.create_oval(x + 6, y + 5, x + 18, y + 17, fill=color, outline=BG)
            cv.create_text(x + 24, y + 11, anchor="w", text=safe(label), fill=BG if on else FG, font=F.small)
            x += pw + 6
        cv.config(height=y + self.ROW)


class App:
    def __init__(self, root, sessions_dir):
        self.root = root
        self.sessions_dir = sessions_dir
        os.makedirs(sessions_dir, exist_ok=True)
        self.client = Ollama()
        self.engine = Engine(self.client)
        self.session = None
        self.models = []
        self.q = queue.Queue()
        self.stop = threading.Event()
        self.busy = False
        self.active = None       # кто говорит прямо сейчас (для полосы очереди)
        self.plan_win = None
        self._live_t = self._wt = 0.0
        self._wtext = ""
        self.guarded = []        # кнопки, которые блокируются, пока идёт генерация
        self.show_thoughts = tk.BooleanVar(value=True)
        self.status = tk.StringVar(value="Готово")
        self.live = tk.StringVar(value="")
        self.series = tk.IntVar(value=5)
        self.kind = tk.StringVar(value="Сцена")
        self.aud_label = tk.StringVar(value="Видят: все")
        self.aud_vars = {}
        self._build()
        root.protocol("WM_DELETE_WINDOW", self._close)
        root.after(80, self._poll)
        root.after(200, self._startup)

    # ---------------------------------------------------------------- построение окна

    def btn(self, parent, text, cmd, style="TButton", guard=True):
        b = ttk.Button(parent, text=text, command=cmd, style=style)
        if guard:
            self.guarded.append(b)
        return b

    def _build(self):
        r = self.root
        r.title("Бункер: чат нейросетей на Ollama")
        sw, sh = int(r.winfo_screenwidth()), int(r.winfo_screenheight())
        r.geometry(f"{min(1580, sw - 40)}x{min(880, sh - 90)}")
        r.minsize(min(1100, sw - 40), min(680, sh - 90))
        r.columnconfigure(1, weight=1)
        r.rowconfigure(0, weight=1)

        menubar = tk.Menu(r)
        m_file = tk.Menu(menubar, tearoff=0)
        m_file.add_command(label="Новая сессия…", command=self.new_session)
        m_file.add_command(label="Открыть сессию…", command=self.open_session_dialog)
        m_file.add_command(label="Открыть папку сессии", command=self.open_folder)
        m_file.add_separator()
        m_file.add_command(label="Выход", command=self._close)
        m_ses = tk.Menu(menubar, tearoff=0)
        m_ses.add_command(label="Настройки сессии…", command=self.settings_dialog)
        m_ses.add_command(label="Итоги сцены → память персонажей…", command=self.summarize)
        m_ses.add_command(label="Экспорт сценария…", command=self.export_dialog)
        m_ses.add_command(label="Обновить список моделей", command=lambda: self.refresh_models(False))
        m_world = tk.Menu(menubar, tearoff=0)
        m_world.add_command(label="План бункера…", command=self.open_plan)
        m_world.add_command(label="Обновить мир сейчас", command=self.world_sync_now)
        m_world.add_command(label="Проверить мир на противоречия", command=self.writer_check)
        menubar.add_cascade(label="Файл", menu=m_file)
        menubar.add_cascade(label="Сессия", menu=m_ses)
        menubar.add_cascade(label="Мир", menu=m_world)
        r.config(menu=menubar)

        # --- слева: персонажи и управление ходом
        left = ttk.Frame(r, padding=(14, 14, 8, 8))
        left.grid(row=0, column=0, sticky="ns")
        ttk.Label(left, text="Персонажи", style="Head.TLabel").pack(anchor="w")
        self.lb = tk.Listbox(left, width=30, height=11, exportselection=False, activestyle="none", bg=FIELD,
                             fg=FG, selectbackground=ACCENT, selectforeground=BG, relief="flat",
                             highlightthickness=0, font=F.bold)
        self.lb.pack(fill="x", pady=(6, 6), ipady=4)
        self.lb.bind("<Double-Button-1>", lambda e: self.speak_selected())
        row = ttk.Frame(left)
        row.pack(fill="x")
        for i, (t, cmd) in enumerate((("Добавить", self.add_char), ("Править", self.edit_char),
                                      ("Удалить", self.del_char))):
            row.columnconfigure(i, weight=1)
            self.btn(row, t, cmd).grid(row=0, column=i, sticky="ew", padx=(0 if i == 0 else 3, 0))
        row = ttk.Frame(left)
        row.pack(fill="x", pady=(4, 0))
        for i, (t, d) in enumerate((("▲ Выше", -1), ("▼ Ниже", 1))):
            row.columnconfigure(i, weight=1)
            self.btn(row, t, lambda d=d: self.move(d)).grid(row=0, column=i, sticky="ew",
                                                          padx=(0 if i == 0 else 3, 0))

        ttk.Label(left, text="Ход", style="Head.TLabel").pack(anchor="w", pady=(20, 6))
        self.btn(left, "Говорит выбранный  ▶", self.speak_selected, "Accent.TButton").pack(fill="x")
        self.btn(left, "Следующий по кругу  ▶", self.speak_next).pack(fill="x", pady=(4, 0))
        self.btn(left, "Режиссёр выбирает  ▶", self.speak_director).pack(fill="x", pady=(4, 0))
        sr = ttk.Frame(left)
        sr.pack(fill="x", pady=(8, 0))
        ttk.Label(sr, text="Реплик подряд:", style="Muted.TLabel").pack(side="left")
        sp = ttk.Spinbox(sr, from_=1, to=50, width=4, textvariable=self.series)
        sp.pack(side="left", padx=6)
        self.guarded.append(sp)
        self.stop_btn = ttk.Button(left, text="■  Стоп", command=self.stop.set)
        self.stop_btn.state(["disabled"])
        self.stop_btn.pack(fill="x", pady=(8, 0))
        self.btn(left, "↶  Удалить последнее", self.undo).pack(fill="x", pady=(16, 0))
        ttk.Checkbutton(left, text="Показывать мысли", variable=self.show_thoughts,
                        command=self.render_all).pack(anchor="w", pady=(14, 0))

        # --- в центре: очередь, журнал и ввод
        mid = ttk.Frame(r, padding=(8, 14, 8, 8))
        mid.grid(row=0, column=1, sticky="nsew")
        mid.rowconfigure(1, weight=1)
        mid.columnconfigure(0, weight=1)
        self.queue = QueueStrip(mid)
        self.queue.cv.grid(row=0, column=0, columnspan=2, sticky="ew", pady=(0, 6))
        self.chat = c = tk.Text(mid, wrap="word", state="disabled", bg=FIELD, fg=FG, relief="flat",
                                highlightthickness=0, padx=22, pady=16, font=F.chat, cursor="arrow",
                                selectbackground="#4a5575")
        sb = ttk.Scrollbar(mid, command=c.yview)
        c.config(yscrollcommand=sb.set)
        c.grid(row=1, column=0, sticky="nsew")
        sb.grid(row=1, column=1, sticky="ns")
        c.tag_configure("say", font=F.chat, lmargin1=14, lmargin2=14, spacing3=3)
        c.tag_configure("scene", font=F.chat_i, foreground="#d6cfae", lmargin1=14, lmargin2=14)
        c.tag_configure("task", font=F.chat, foreground="#f0d9a0", lmargin1=14, lmargin2=14)
        c.tag_configure("lab", font=F.small, foreground=MUTED, spacing1=10)
        c.tag_configure("thought", font=F.chat_i, foreground=THOUGHT, lmargin1=28, lmargin2=28)
        c.tag_configure("meta", font=F.small, foreground=MUTED, lmargin1=14, lmargin2=14)

        self.live_lbl = ttk.Label(mid, textvariable=self.live, style="Live.TLabel", justify="left", wraplength=600)
        self.live_lbl.grid(row=2, column=0, columnspan=2, sticky="ew", pady=(8, 0))
        mid.bind("<Configure>", lambda e: self.live_lbl.config(wraplength=max(200, e.width - 40)))

        comp = ttk.Frame(mid)
        comp.grid(row=3, column=0, columnspan=2, sticky="ew", pady=(8, 0))
        comp.columnconfigure(0, weight=1)
        bar = ttk.Frame(comp)
        bar.grid(row=0, column=0, sticky="ew")
        ttk.Combobox(bar, textvariable=self.kind, values=["Сцена", "Задача"], state="readonly",
                     width=9).pack(side="left")
        self.aud_btn = ttk.Menubutton(bar, textvariable=self.aud_label, width=34)
        self.aud_menu = tk.Menu(self.aud_btn, tearoff=0)
        self.aud_btn["menu"] = self.aud_menu
        self.aud_btn.pack(side="left", padx=8)
        self.btn(bar, "Отправить  (Ctrl+Enter)", self.send, "Accent.TButton").pack(side="right")
        self.inp = mk_text(comp, 3)
        self.inp.grid(row=1, column=0, sticky="ew", pady=(8, 0))
        self.inp.bind("<Control-Return>", lambda e: (self.send(), "break")[1])

        # --- справа: сценарист и задачи
        right = ttk.Frame(r, padding=(4, 14, 14, 8))
        right.grid(row=0, column=2, sticky="ns")
        nb = ttk.Notebook(right)
        nb.pack(fill="both", expand=True)
        self.writer = WriterPanel(self, nb)
        self.tasks_panel = TasksPanel(self, nb)
        nb.add(self.writer.frame, text="Сценарист")
        nb.add(self.tasks_panel.frame, text="Задачи")

        ttk.Label(r, textvariable=self.status, style="Muted.TLabel", padding=(14, 5)).grid(
            row=1, column=0, columnspan=3, sticky="ew")

    # ---------------------------------------------------------------- фоновые задачи

    def post(self, fn, *a):
        self.q.put((fn, a))

    def _poll(self):
        while True:
            try:
                fn, a = self.q.get_nowait()
            except queue.Empty:
                break
            try:
                fn(*a)
            except Exception:  # noqa: BLE001 — ошибка одного обновления не должна убить опрос очереди
                traceback.print_exc()
        self.root.after(80, self._poll)

    def status_cb(self, text):
        self.post(self.status.set, safe(text))

    def live_cb(self, name, thoughts, words):
        now = time.monotonic()
        if now - self._live_t < 0.1:
            return
        self._live_t = now
        text = f"{name} · мысли: {thoughts}" if thoughts else f"{name} думает…"
        if words:
            text += f"\n{name} · слова: {words}"
        self.post(self.live.set, safe(text))

    def set_busy(self, b):
        self.busy = b
        for w in self.guarded:
            w.state(["disabled" if b else "!disabled"])
        self.stop_btn.state(["!disabled" if b else "disabled"])

    def run_job(self, work, done=None):
        if self.busy:
            return
        self.stop.clear()
        self.set_busy(True)

        def target():
            try:
                res = work()
                self.post(self._finish, done, res, None, self.stop.is_set())
            except Exception as e:  # noqa: BLE001
                if not isinstance(e, (OllamaError, EngineError)):
                    traceback.print_exc()
                self.post(self._finish, None, None, e, self.stop.is_set())

        threading.Thread(target=target, daemon=True).start()

    def _finish(self, done, res, err, stopped=False):
        self.set_busy(False)
        self.live.set("")
        self.writer.live.set("")
        self.active = None
        self.refresh_queue()
        if stopped:
            self.status.set("Остановлено")
            return
        if err:
            self.status.set("Ошибка: " + safe(err))
            messagebox.showerror("Ошибка", str(err))
            return
        self.status.set("Готово")
        if done:
            done(res)

    def _close(self):
        self.stop.set()
        self.root.destroy()

    # ---------------------------------------------------------------- сессии

    def ready(self):
        if self.session is None:
            messagebox.showinfo("Нет сессии", "Сначала создайте или откройте сессию: меню «Файл».")
            return False
        return True

    def _startup(self):
        self.refresh_models(quiet=False)
        names = list_sessions(self.sessions_dir)
        if names:
            self.load(os.path.join(self.sessions_dir, names[0]))
        else:
            self.new_session()

    def refresh_models(self, quiet=True, done=None):
        if getattr(self, "models_loading", False):
            if done:
                self.models_waiters.append(done)
            if not quiet:
                self.models_quiet = False
            return
        self.models_loading = True
        self.models_quiet = quiet
        self.models_waiters = [done] if done else []
        self.status.set("Проверяю подключение к Ollama…")

        def work():
            try:
                self.post(self._models_done, self.client.models(), None)
            except OllamaError as e:
                self.post(self._models_done, [], e)

        threading.Thread(target=work, daemon=True).start()

    def _models_done(self, models, err):
        self.models_loading = False
        self.models = models
        if err:
            self.status.set(safe(err))
            if not self.models_quiet:
                messagebox.showwarning("Ollama недоступна", f"{err}\n\nЗапустите Ollama, затем выберите "
                                       "«Файл → Обновить список моделей».")
        else:
            self.status.set(f"Ollama на связи, моделей: {len(models)}")
        waiters, self.models_waiters = self.models_waiters, []
        for done in waiters:
            done()

    def new_session(self):
        if self.busy:
            return
        name = simpledialog.askstring("Новая сессия", "Название сессии (станет именем папки):", parent=self.root)
        if not name or not name.strip():
            return
        try:
            s = Session.create(self.sessions_dir, name.strip())
        except (ValueError, OSError) as e:
            messagebox.showerror("Новая сессия", str(e))
            return
        self.load(s.path)
        self.root.after(150, self.settings_dialog)

    def open_session_dialog(self):
        if self.busy:
            return
        names = list_sessions(self.sessions_dir)
        if not names:
            messagebox.showinfo("Сессии", "Сохранённых сессий пока нет.")
            return
        top = modal(self, "Открыть сессию", 400, 380)
        lb = tk.Listbox(top, bg=FIELD, fg=FG, selectbackground=ACCENT, selectforeground=BG, relief="flat",
                        highlightthickness=0, font=F.base, activestyle="none")
        lb.pack(fill="both", expand=True, padx=14, pady=14)
        for n in names:
            lb.insert("end", n)

        def go():
            sel = lb.curselection()
            if sel:
                top.destroy()
                self.load(os.path.join(self.sessions_dir, names[sel[0]]))

        lb.bind("<Double-Button-1>", lambda e: go())
        ttk.Button(top, text="Открыть", style="Accent.TButton", command=go).pack(pady=(0, 14))

    def load(self, path):
        try:
            s = Session(path)
        except Exception as e:  # noqa: BLE001
            messagebox.showerror("Сессия", f"Не удалось открыть сессию:\n{e}")
            return
        if self.plan_win:
            self.plan_win.close()
        self.session = s
        self.root.title(f"Бункер: {s.name}")
        self.aud_vars = {}
        self.refresh_roster()
        self.render_all()
        self.writer.render_all()
        self.tasks_panel.refresh()
        self.status.set(f"Сессия «{s.name}»: персонажей — {len(s.characters)}, сообщений — {len(s.log)}")
        if s.load_problems:
            messagebox.showwarning(
                "Повреждённые данные",
                "Некоторые файлы не удалось прочитать. Вместо них загружены пустые данные или настройки "
                "по умолчанию. Исходники сохранены в резервных копиях:\n\n" + "\n\n".join(s.load_problems),
                parent=self.root)

    def open_folder(self):
        if not self.ready():
            return
        p = self.session.path
        if sys.platform.startswith("win"):
            os.startfile(p)  # noqa: S606
        elif sys.platform == "darwin":
            subprocess.Popen(["open", p])
        else:
            subprocess.Popen(["xdg-open", p])

    # ---------------------------------------------------------------- список персонажей и очередь

    def refresh_roster(self, select=None):
        s = self.session
        self.lb.delete(0, "end")
        names = s.order if s else []
        for i, n in enumerate(names):
            c = s.characters[n]
            self.lb.insert("end", f" {n}   ·   {c.model or 'модель не выбрана'}")
            self.lb.itemconfig(i, foreground=c.color)
        if select in names:
            self.lb.selection_set(names.index(select))
        old = {n for n, v in self.aud_vars.items() if v.get()}
        self.aud_menu.delete(0, "end")
        self.aud_vars = {}
        for n in names:
            v = tk.BooleanVar(value=n in old)
            self.aud_vars[n] = v
            self.aud_menu.add_checkbutton(label=n, variable=v, command=self._aud_changed)
        self._aud_changed()
        self.refresh_queue()
        if self.plan_win:
            self.plan_win.refresh()

    def refresh_queue(self):
        s = self.session
        if not s:
            self.queue.update([], None, None, None)
            return
        nxt = None
        if s.order and s.settings.get("director_mode", "rules") == "rules":
            try:
                nxt = rules_pick(s)[0]
            except EngineError:
                nxt = None
        self.queue.update([(n, s.characters[n].color) for n in s.order], last_speaker(s), nxt, self.active)

    def set_active(self, name):
        self.active = name
        self.refresh_queue()

    def _aud_changed(self):
        sel = [n for n, v in self.aud_vars.items() if v.get()]
        self.aud_label.set("Видят: " + (", ".join(sel) if sel else "все"))

    def selected(self):
        sel = self.lb.curselection()
        return self.session.order[sel[0]] if sel and self.session else None

    def add_char(self):
        if self.ready():
            self.refresh_models(done=lambda: CharDialog(self))

    def edit_char(self):
        if not self.ready():
            return
        n = self.selected()
        if not n:
            messagebox.showinfo("Выберите персонажа", "Выделите персонажа в списке слева.")
            return
        self.refresh_models(done=lambda: CharDialog(self, n))

    def del_char(self):
        if not self.ready():
            return
        n = self.selected()
        if n and messagebox.askyesno("Удалить персонажа", f"Удалить «{n}» вместе с папкой его памяти?\n"
                                     "Сообщения в журнале останутся."):
            self.session.delete_character(n)
            self.refresh_roster()
            self.render_all()

    def move(self, d):
        n = self.selected()
        if not n:
            return
        o = self.session.order
        i = o.index(n)
        j = i + d
        if 0 <= j < len(o):
            o[i], o[j] = o[j], o[i]
            self.session.save_meta()
            self.refresh_roster(select=n)

    # ---------------------------------------------------------------- журнал на экране

    def _ctag(self, name):
        tag = "c:" + name
        c = self.session.characters.get(name) if self.session else None
        self.chat.tag_configure(tag, foreground=c.color if c else MUTED, font=F.name, spacing1=10)
        return tag

    def render_all(self):
        t = self.chat
        t.config(state="normal")
        t.delete("1.0", "end")
        t.config(state="disabled")
        if self.session:
            for m in self.session.log:
                self.render_msg(m)

    def render_msg(self, m):
        t = self.chat
        t.config(state="normal")
        k = m.get("kind")
        if k in ("scene", "task"):
            lab = "Сцена" if k == "scene" else "Задача"
            if m.get("audience"):
                lab += " (только для: " + ", ".join(m["audience"]) + ")"
            t.insert("end", safe(lab) + "\n", ("lab",))
            t.insert("end", safe(m["text"]) + "\n", (k,))
        else:
            who = m.get("speaker", "?")
            t.insert("end", safe(who) + "\n", (self._ctag(who),))
            t.insert("end", safe(m["text"]) + "\n", ("say",))
            if m.get("thought") and self.show_thoughts.get():
                t.insert("end", "мысли: " + safe(m["thought"]) + "\n", ("thought",))
            d = m.get("delta")
            if d:
                bits = ["+" + x for x in d.get("add", [])] + ["−" + x for x in d.get("remove", [])]
                if d.get("state"):
                    bits.append("состояние: " + d["state"])
                if bits:
                    t.insert("end", "лист: " + safe("; ".join(bits)) + "\n", ("meta",))
        t.config(state="disabled")
        t.see("end")

    def note(self, text):
        t = self.chat
        t.config(state="normal")
        t.insert("end", safe(text) + "\n", ("meta",))
        t.config(state="disabled")
        t.see("end")

    def on_message(self, m):
        self.render_msg(m)
        self.refresh_queue()

    # ---------------------------------------------------------------- ходы

    def _series(self):
        try:
            return max(1, int(self.series.get()))
        except (tk.TclError, ValueError):
            return 1

    def _turns(self, chooser, count):
        s = self.session

        def work():
            for _ in range(count):
                if self.stop.is_set():
                    break
                name, why = chooser()
                if self.stop.is_set():
                    break
                if why:
                    self.post(self.note, f"Режиссёр даёт слово: {name}. {why}")
                self.post(self.set_active, name)
                m = self.engine.turn(s, s.characters[name], self.stop, self.status_cb,
                                     lambda th, w, n=name: self.live_cb(n, th, w))
                if m:
                    self.post(self.on_message, m)

        self.run_job(work, lambda _: self.maybe_auto_sync())

    def speak_selected(self):
        if not self.ready():
            return
        n = self.selected()
        if not n:
            messagebox.showinfo("Выберите персонажа", "Выделите персонажа в списке слева.")
            return
        self._turns(lambda: (n, ""), 1)

    def speak_next(self):
        if self.ready():
            s = self.session
            self._turns(lambda: next_in_order(s), self._series())

    def speak_director(self):
        if self.ready():
            s = self.session
            self._turns(lambda: self.engine.pick_speaker(s, self.stop, self.status_cb), self._series())

    def undo(self):
        if self.ready() and self.session.log:
            self.session.pop_last()
            self.render_all()
            self.refresh_queue()
            self.status.set("Последнее сообщение удалено, лист и мысли откатены")

    def send(self):
        if not self.ready() or self.busy:
            return
        text = self.inp.get("1.0", "end").strip()
        if not text:
            return
        kind = "scene" if self.kind.get() == "Сцена" else "task"
        aud = [n for n, v in self.aud_vars.items() if v.get()] or None
        self.on_message(self.session.add_message(kind, text, audience=aud))
        self.inp.delete("1.0", "end")
        for v in self.aud_vars.values():  # тайное сообщение не должно «залипнуть» на следующее
            v.set(False)
        self._aud_changed()

    def compose(self, kind, text, audience):
        """Положить текст в поле ввода (например, вариант от сценариста); отправляет автор."""
        self.kind.set("Сцена" if kind == "scene" else "Задача")
        self.inp.delete("1.0", "end")
        self.inp.insert("1.0", text)
        for n, v in self.aud_vars.items():
            v.set(bool(audience) and n in audience)
        self._aud_changed()
        self.inp.focus_set()

    # ---------------------------------------------------------------- план и мир

    def open_plan(self):
        if not self.ready():
            return
        if self.plan_win:
            self.plan_win.top.lift()
            return
        self.plan_win = PlanWindow(self)

    def world_changed(self, source=None):
        if self.plan_win and self.plan_win is not source:
            self.plan_win.refresh()
        self.refresh_queue()

    def _writer_ready(self):
        if not self.ready() or self.busy:
            return False
        if not writer_model(self.session):
            messagebox.showinfo("Сценарист", "Выберите модель сценариста: Сессия → Настройки сессии.")
            return False
        return True

    def _wnote(self, text):
        self.session.add_writer_msg("note", text)
        self.writer.render_all()

    # ---------------------------------------------------------------- сценарист

    def _writer_tok(self, piece):  # вызывается из рабочего потока
        self._wtext += piece
        now = time.monotonic()
        if now - self._wt > 0.12:
            self._wt = now
            self.post(self.writer.live.set, safe(self._wtext[-600:]))

    def _writer_answer(self, text):
        self.writer.live.set("")
        if text:
            self.session.add_writer_msg("ai", text)
            self.writer.render_all()

    def writer_ask(self):
        if not self._writer_ready():
            return
        text = self.writer.take_input()
        if not text:
            return
        s = self.session
        history = [(m["role"], m["text"]) for m in s.writer_log if m["role"] in ("user", "ai")]
        s.add_writer_msg("user", text)
        self.writer.render_all()
        self._wtext, self._wt = "", 0.0
        self.run_job(lambda: self.engine.ask_writer(s, history, text, self.stop, self.status_cb, self._writer_tok),
                     self._writer_answer)

    def writer_check(self):
        if not self._writer_ready():
            return
        s = self.session
        s.add_writer_msg("user", "Проверь мир на противоречия.")
        self.writer.render_all()
        self._wtext, self._wt = "", 0.0
        self.run_job(lambda: self.engine.check_world(s, self.stop, self.status_cb, self._writer_tok),
                     self._writer_answer)

    def writer_next_step(self, task_id=0, hint=None):
        if not self._writer_ready():
            return
        if hint is None:
            hint = self.writer.take_input()
        s = self.session
        self.run_job(lambda: self.engine.story_options(s, hint, task_id, self.stop, self.status_cb),
                     self._show_options)

    def _show_options(self, options):
        self._wnote("Сценарист предложил варианты: " + "; ".join(o["title"] for o in options) + ".")
        OptionsDialog(self, options)

    def writer_edit_plan(self):
        if not self._writer_ready():
            return
        text = self.writer.take_input()
        if not text:
            messagebox.showinfo("Изменить план", "Напишите в поле, что изменить обычными словами, например: "
                                "«добавь склад за генераторной» или «насос теперь протекает».")
            return
        s = self.session
        s.add_writer_msg("user", "[план] " + text)
        self.writer.render_all()
        self.run_job(lambda: self.engine.plan_edit(s, text, self.stop, self.status_cb), self._show_ops)

    def _show_ops(self, res):
        summary, ops = res
        if not ops:
            self._wnote("Сценарист не нашёл, что менять. Уточните просьбу.")
            return
        OpsDialog(self, summary, ops)

    def apply_plan_ops(self, ops):
        s = self.session
        done, skipped = s.world.apply_ops(ops, list(s.order))
        s.save_world()
        text = "План изменён: " + ("; ".join(done) if done else "ничего") + "."
        if skipped:
            text += "\nНе удалось: " + "; ".join(skipped) + "."
        self._wnote(text)
        self.world_changed()

    def world_sync_now(self, auto=False):
        """Сценарист читает новые события и обновляет, кто где и что с объектами."""
        auto = auto is True
        if not self.ready() or self.busy:
            return
        s = self.session
        if not auto:
            if not writer_model(s):
                messagebox.showinfo("Сценарист", "Выберите модель сценариста: Сессия → Настройки сессии.")
                return
            if not s.world.rooms:
                messagebox.showinfo("Обновить мир", "Сначала создайте на плане хотя бы одну комнату.")
                return
        self.run_job(lambda: self.engine.sync_world(s, self.stop, self.status_cb),
                     lambda res: self._sync_done(res, auto))

    def _sync_done(self, res, auto):
        s = self.session
        if not res:
            if not auto:
                self.status.set("Новых событий для обновления мира нет")
            return
        report, warns = s.world.apply_sync(res, list(s.order))
        hit = s.tasks.suggest_done(res["done_tasks"])
        s.save_world()
        if auto and not (report or warns or hit or res["note"]):
            return
        lines = ["Мир обновлён: " + "; ".join(report) + "." if report else "Мир проверен, изменений нет."]
        lines += ["⚠ " + w for w in warns]
        if hit:
            lines.append("Похоже, выполнены задачи: " + ", ".join(f"#{t['id']}" for t in hit)
                         + ". Закройте их во вкладке «Задачи».")
        if res["note"]:
            lines.append("Сценарист: " + res["note"])
        self._wnote("\n".join(lines))
        self.tasks_panel.refresh()
        self.world_changed()

    def maybe_auto_sync(self):
        """После серии реплик: если накопилось достаточно новых сообщений, сценарист обновляет мир."""
        s = self.session
        if not s or self.busy or self.stop.is_set():
            return
        n = int(s.settings.get("sync_every") or 0)
        if n > 0 and s.world.rooms and s.characters and writer_model(s) and len(s.log) - s.world.synced_upto >= n:
            self.world_sync_now(auto=True)

    # ---------------------------------------------------------------- окна сессии

    def settings_dialog(self):
        if self.ready() and not self.busy:
            self.refresh_models(done=lambda: SettingsDialog(self))

    def summarize(self):
        if not self.ready() or self.busy:
            return
        s = self.session
        self.run_job(lambda: self.engine.propose_memories(s, self.stop, self.status_cb), self._show_proposals)

    def _show_proposals(self, res):
        if res:
            SummaryDialog(self, res)
        else:
            messagebox.showinfo("Итоги сцены", "Новых событий для памяти нет.")

    def export_dialog(self):
        if self.ready() and not self.busy:
            self.refresh_models(done=lambda: ExportDialog(self))

    def export_script(self, model, thoughts):
        s = self.session
        self.run_job(lambda: self.engine.export_script(s, model, thoughts, self.stop, self.status_cb),
                     self._show_script)

    def _show_script(self, res):
        path, text = res
        if path:
            ScriptWindow(self, path, text)
            self.status.set("Сценарий сохранён: " + path)
        else:
            messagebox.showinfo("Сценарий", "Сценарий не создан.")
