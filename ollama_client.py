"""Мини-клиент Ollama на стандартной библиотеке: ничего ставить через pip не нужно."""
from __future__ import annotations

import json
import os
import urllib.error
import urllib.request

# Адрес можно переопределить переменной окружения BUNKER_OLLAMA_URL
DEFAULT_URL = os.environ.get("BUNKER_OLLAMA_URL", "http://127.0.0.1:11434")


class OllamaError(Exception):
    pass


def _norm(name):
    name = name or ""
    return name if ":" in name else name + ":latest"


class Ollama:
    def __init__(self, url=DEFAULT_URL):
        self.url = url.rstrip("/")

    def _open(self, path, payload=None, timeout=900):
        data = json.dumps(payload).encode("utf-8") if payload is not None else None
        req = urllib.request.Request(self.url + path, data=data,
                                     headers={"Content-Type": "application/json"})
        try:
            return urllib.request.urlopen(req, timeout=timeout)
        except urllib.error.HTTPError as e:
            body = e.read().decode("utf-8", "replace")
            try:
                body = json.loads(body).get("error", body)
            except (ValueError, AttributeError):
                pass
            raise OllamaError(f"Ollama ответила {e.code}: {body}") from None
        except OSError as e:
            raise OllamaError(f"Нет связи с Ollama по адресу {self.url}. "
                              f"Она запущена? ({getattr(e, 'reason', e)})") from None

    # ---------- модели

    def models(self):
        with self._open("/api/tags", timeout=10) as r:
            return sorted(m["name"] for m in json.load(r).get("models", []))

    def loaded(self):
        try:
            with self._open("/api/ps", timeout=10) as r:
                return [m.get("name") or m.get("model") for m in json.load(r).get("models", [])]
        except OllamaError:
            return []

    def unload(self, model):
        try:
            self._open("/api/generate", {"model": model, "keep_alive": 0}, timeout=60).close()
        except OllamaError:
            pass

    def only_loaded(self, model):
        """Последовательная загрузка: выгрузить из памяти всё, кроме нужной модели."""
        for name in self.loaded():
            if name and _norm(name) != _norm(model):
                self.unload(name)

    # ---------- чат

    def chat(self, model, messages, options=None, fmt=None, stream=True, on_token=None, stop=None):
        """Вернуть текст ответа. fmt — JSON-схема (структурированный вывод).
        on_token(кусок) вызывается по ходу генерации, stop — threading.Event для отмены."""
        payload = {"model": model, "messages": messages, "stream": stream,
                   "think": False, "options": options or {}}
        if fmt:
            payload["format"] = fmt
        try:
            resp = self._open("/api/chat", payload)
        except OllamaError as e:
            if "think" not in str(e).lower():
                raise
            payload.pop("think")  # модель не поддерживает параметр think
            resp = self._open("/api/chat", payload)
        parts = []
        with resp:
            if not stream:
                return json.load(resp).get("message", {}).get("content", "")
            for line in resp:
                if stop is not None and stop.is_set():
                    break  # выход закрывает соединение, Ollama прекращает генерацию
                line = line.strip()
                if not line:
                    continue
                chunk = json.loads(line)
                if chunk.get("error"):
                    raise OllamaError(chunk["error"])
                piece = chunk.get("message", {}).get("content", "")
                if piece:
                    parts.append(piece)
                    if on_token:
                        on_token(piece)
                if chunk.get("done"):
                    break
        return "".join(parts)
