"""Запуск: python main.py"""
import os
import sys
import tkinter as tk

from gui import App
from ui import apply_theme


def main():
    if sys.platform.startswith("win"):
        try:  # чёткий текст на мониторах с масштабированием
            import ctypes
            ctypes.windll.shcore.SetProcessDpiAwareness(1)
        except Exception:  # noqa: BLE001
            pass
    root = tk.Tk()
    apply_theme(root)
    App(root, os.path.join(os.path.dirname(os.path.abspath(__file__)), "sessions"))
    root.mainloop()


if __name__ == "__main__":
    main()
