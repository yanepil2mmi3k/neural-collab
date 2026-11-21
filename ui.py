"""Общие стили и мелкие помощники интерфейса."""
from __future__ import annotations

import tkinter as tk
import tkinter.font as tkfont
from tkinter import ttk

# Бетон бункера, бумага сценария и одна лампа аварийного света — для главного действия.
BG, PANEL, FIELD = "#1e2220", "#282d2a", "#313733"
FG, MUTED, ACCENT, THOUGHT = "#e7e3d7", "#8f9788", "#d9a441", "#9fb3c8"


class _Fonts:
    base = bold = small = chat = chat_i = name = None


F = _Fonts()  # шрифты создаются в apply_theme, поэтому обращаться к ним нужно как F.base, F.small…


def _pick(root, candidates, fallback):
    have = set(tkfont.families(root))
    return next((f for f in candidates if f in have), fallback)


def apply_theme(root):
    base = tkfont.nametofont("TkDefaultFont").actual("family")
    ui = _pick(root, ("Segoe UI", "SF Pro Text", "Noto Sans", "DejaVu Sans", "Arial"), base)
    serif = _pick(root, ("Georgia", "Cambria", "Noto Serif", "DejaVu Serif", "Times New Roman"), ui)
    F.base = tkfont.Font(root=root, family=ui, size=10)
    F.bold = F.name = tkfont.Font(root=root, family=ui, size=10, weight="bold")
    F.small = tkfont.Font(root=root, family=ui, size=9)
    F.chat = tkfont.Font(root=root, family=serif, size=12)  # реплики читаются как страница сценария
    F.chat_i = tkfont.Font(root=root, family=serif, size=12, slant="italic")
    root.configure(bg=BG)
    for opt, val in (("background", FIELD), ("foreground", FG), ("selectBackground", ACCENT),
                     ("selectForeground", BG)):
        root.option_add("*TCombobox*Listbox." + opt, val)
    for opt, val in (("background", FIELD), ("foreground", FG), ("activeBackground", ACCENT),
                     ("activeForeground", BG)):
        root.option_add("*Menu." + opt, val)
    st = ttk.Style(root)
    st.theme_use("clam")
    st.configure(".", background=BG, foreground=FG, fieldbackground=FIELD, bordercolor=PANEL,
                 lightcolor=PANEL, darkcolor=PANEL, troughcolor=PANEL, font=F.base, focuscolor=BG)
    st.configure("TButton", background=PANEL, foreground=FG, padding=(10, 6), borderwidth=0, focusthickness=0)
    st.map("TButton", background=[("disabled", BG), ("active", FIELD)], foreground=[("disabled", MUTED)])
    st.configure("Accent.TButton", background=ACCENT, foreground=BG, font=F.bold)
    st.map("Accent.TButton", background=[("disabled", PANEL), ("active", "#e8b655")],
           foreground=[("disabled", MUTED)])
    st.configure("TLabel", background=BG, foreground=FG)
    st.configure("Head.TLabel", foreground=ACCENT, font=F.bold)
    st.configure("Muted.TLabel", foreground=MUTED, font=F.small)
    st.configure("Live.TLabel", foreground=THOUGHT, font=F.small)
    st.configure("TEntry", fieldbackground=FIELD, foreground=FG, insertcolor=FG, padding=5)
    st.configure("TCombobox", fieldbackground=FIELD, background=PANEL, foreground=FG, arrowcolor=FG, padding=4)
    st.map("TCombobox", fieldbackground=[("readonly", FIELD)], foreground=[("readonly", FG)],
           selectbackground=[("readonly", FIELD)], selectforeground=[("readonly", FG)])
    st.configure("TSpinbox", fieldbackground=FIELD, foreground=FG, arrowcolor=FG, padding=4)
    st.configure("TCheckbutton", background=BG, foreground=FG)
    st.map("TCheckbutton", background=[("active", BG)],
           indicatorcolor=[("selected", ACCENT), ("!selected", FIELD)])
    st.configure("TMenubutton", background=PANEL, foreground=FG, arrowcolor=FG, padding=(8, 5))
    st.configure("TNotebook", background=BG, borderwidth=0)
    st.configure("TNotebook.Tab", background=PANEL, foreground=MUTED, padding=(14, 7), borderwidth=0)
    st.map("TNotebook.Tab", background=[("selected", FIELD)], foreground=[("selected", FG)])
    st.configure("TScrollbar", background=FIELD, troughcolor=PANEL, arrowcolor=MUTED, bordercolor=PANEL)


def safe(text):
    """Tk плохо переваривает символы вне BMP (эмодзи) и управляющие коды — для показа убираем их."""
    return "".join(ch for ch in str(text) if ord(ch) <= 0xFFFF and (ch >= " " or ch in "\n\t"))


def mk_text(parent, height=3, **kw):
    return tk.Text(parent, height=height, wrap="word", bg=FIELD, fg=FG, insertbackground=FG, relief="flat",
                   highlightthickness=1, highlightbackground=PANEL, highlightcolor=ACCENT,
                   selectbackground="#4a5575", padx=8, pady=6, font=F.base, undo=True, **kw)


def modal(app, title, w, h, grab=True):
    """Диалог поверх главного окна. grab=False — окно не блокирует главное."""
    top = tk.Toplevel(app.root)
    top.title(title)
    top.configure(bg=BG)
    top.geometry(f"{w}x{h}+{app.root.winfo_rootx() + 50}+{app.root.winfo_rooty() + 30}")
    top.transient(app.root)
    top.update_idletasks()
    if grab:
        try:
            top.grab_set()
        except tk.TclError:
            pass
    return top


def lines_of(widget):
    return [x.strip() for x in widget.get("1.0", "end").splitlines() if x.strip()]
