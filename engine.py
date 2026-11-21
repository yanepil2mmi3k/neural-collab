"""Логика без интерфейса: промпты, ходы персонажей, режиссёр, итоги в память, сценарий."""
from __future__ import annotations

import copy
import json
import os
import re
from datetime import datetime

from ollama_client import OllamaError
from storage import FIELDS
from world import OPS, norm


class EngineError(Exception):
    pass


# Ответ персонажа — всегда JSON: так он не путает «мысли» и «слова», а Ollama гарантирует формат.
TURN_SCHEMA = {
    "type": "object",
    "properties": {"thoughts": {"type": "string"}, "words": {"type": "string"}},
    "required": ["thoughts", "words"],
}
UPDATE_SCHEMA = {
    "type": "object",
    "properties": {
        "inventory_add": {"type": "array", "items": {"type": "string"}},
        "inventory_remove": {"type": "array", "items": {"type": "string"}},
        "state": {"type": "string"},
    },
    "required": ["inventory_add", "inventory_remove", "state"],
}


OPTIONS_SCHEMA = {
    "type": "object",
    "properties": {"options": {"type": "array", "items": {
        "type": "object",
        "properties": {"title": {"type": "string"}, "scene": {"type": "string"},
                       "secret_to": {"type": "array", "items": {"type": "string"}},
                       "secret": {"type": "string"}, "task": {"type": "integer"}},
        "required": ["title", "scene", "secret_to", "secret", "task"]}}},
    "required": ["options"],
}
OPS_SCHEMA = {
    "type": "object",
    "properties": {
        "summary": {"type": "string"},
        "ops": {"type": "array", "items": {
            "type": "object",
            "properties": {"op": {"type": "string", "enum": list(OPS)}, "room": {"type": "string"},
                           "object": {"type": "string"}, "target": {"type": "string"}, "text": {"type": "string"}},
            "required": ["op", "room", "object", "target", "text"]}},
    },
    "required": ["summary", "ops"],
}


def _noop(*_):
    pass


# ---------------------------------------------------------------- журнал и видимость

def strip_think(text):
    return re.sub(r"(?s)<think>.*?(?:</think>|$)", "", text or "").strip()


def can_see(m, who):
    aud = m.get("audience")
    return not aud or who in aud or m.get("speaker") == who


def fmt_msg(m):
    tag = "(тайно, только для: %s) " % ", ".join(m["audience"]) if m.get("audience") else ""
    if m["kind"] == "scene":
        return f"{tag}[СЦЕНА] {m['text']}"
    if m["kind"] == "task":
        return f"{tag}[ЗАДАЧА] {m['text']}"
    return f"{m['speaker']}: {m['text']}"


def recent_log(s, who, n):
    return [m for m in s.log if can_see(m, who)][-int(n):]


def last_speaker(s):
    return next((m["speaker"] for m in reversed(s.log)
                 if m["kind"] == "say" and m["speaker"] in s.characters), None)


def next_in_order(s):
    """Следующий по кругу после последнего говорившего. Возвращает (имя, пояснение)."""
    if not s.order:
        raise EngineError("В сессии пока нет персонажей.")
    last = last_speaker(s)
    if last not in s.order:
        return s.order[0], ""
    return s.order[(s.order.index(last) + 1) % len(s.order)], ""


def _stem(name):
    w = norm((name.split() or [""])[0])
    return w[:-1] if len(w) > 3 and w[-1] in "аяйьеоиыую" else w


def mentioned(text, name):
    """Назвали ли персонажа по имени (с учётом падежей: Мария → Марии, Марию; Иван → Ивана)."""
    st = _stem(name)
    return bool(st) and re.search(r"(?<![a-zа-я0-9])" + re.escape(st), norm(text)) is not None


def rules_pick(s):
    """Режиссёр без модели: сначала тот, к кому обратились по имени, затем свои по комнате,
    затем тот, кто дольше всех молчит. Возвращает (имя, причина)."""
    names = list(s.order)
    if not names:
        raise EngineError("В сессии пока нет персонажей.")
    if len(names) == 1:
        return names[0], ""
    last = last_speaker(s)
    others = [n for n in names if n != last] or names
    public = [m for m in s.log if not m.get("audience")]
    if public:
        top = public[-1]
        hit = [n for n in others if n != top.get("speaker") and mentioned(top["text"], n)]
        if hit:
            return hit[0], "к персонажу обратились по имени"
    room = s.world.positions.get(last) if last else None
    if room:
        same = [n for n in others if s.world.positions.get(n) == room]
        if same:
            others = same

    def silent_since(n):
        idx = [i for i, m in enumerate(s.log) if m["kind"] == "say" and m["speaker"] == n]
        return idx[-1] if idx else -1

    return min(others, key=silent_since), "дольше всех молчит"


def writer_model(s):
    """Модель сценариста; если не выбрана, берём модель режиссёра."""
    st = s.settings
    return (st.get("scribe_model") or st.get("director_model") or "").strip()


def writer_events(s, start=0, n=30, thoughts=False):
    """Журнал для сценариста: он видит всё, включая тайные сообщения и (по желанию) мысли персонажей."""
    out = []
    for m in s.log[start:]:
        line = fmt_msg(m)
        if thoughts and m.get("thought"):
            line += f"\n    (мысли {m['speaker']}: {m['thought']})"
        out.append(line)
    return "\n".join(out[-int(n):]) or "(пока ничего не произошло)"


def writer_system(s):
    st = s.settings
    cast = []
    for n in s.order:
        c = s.characters[n]
        room = s.world.where(n)
        bits = [c.sheet.get("profession") or "", f"где: {room['name'] if room else 'не на плане'}"]
        if c.sheet.get("state"):
            bits.append(f"состояние: {c.sheet['state']}")
        if c.sheet.get("inventory"):
            bits.append("инвентарь: " + ", ".join(c.sheet["inventory"]))
        cast.append(f"- {n}; " + "; ".join(b for b in bits if b))
    tasks = "\n".join(f"#{t['id']} {t['text']}" for t in s.tasks.open_items()) or "(нет)"
    return (
        "Ты — сценарист и хранитель мира. Автор (человек) снимает сериал-эксперимент: нейросетевые персонажи "
        "заперты в бункере и реагируют на сцены. Ты помогаешь автору: знаешь план бункера, где кто находится, "
        "скрытую правду, и предлагаешь развитие сюжета. Ты видишь то, чего персонажи не видят: тайные сообщения, "
        "их мысли, скрытые объекты. Решения принимает автор, ты предлагаешь. Отвечай по-русски, коротко и по делу.\n\n"
        f"СЕТТИНГ:\n{st['setting'].strip() or '(не задан)'}\n\n"
        f"ПРАВДА АВТОРА (персонажи этого не знают):\n{st.get('truth', '').strip() or '(не задана)'}\n\n"
        f"ПЛАН БУНКЕРА:\n{s.world.full_text(list(s.order))}\n\n"
        f"ПЕРСОНАЖИ:\n{chr(10).join(cast) or '(пока нет)'}\n\n"
        f"ЗАДАЧИ АВТОРА, ЧТО ОН ХОЧЕТ УВИДЕТЬ (персонажам они неизвестны):\n{tasks}"
    )


def parse_json(raw):
    try:
        d = json.loads(strip_think(raw))
    except ValueError:
        d = None
    if not isinstance(d, dict):
        raise EngineError("Модель ответила не в том формате. Повторите запрос или выберите другую модель.")
    return d


# ---------------------------------------------------------------- промпт персонажа

def sheet_text(c):
    lines = []
    for key, label, _kind, _h in FIELDS:
        v = c.sheet.get(key)
        if not v:
            continue
        v = "; ".join(v) if isinstance(v, list) else " ".join(str(v).split())
        lines.append(f"{label}: {v}")
    return "\n".join(lines) or "(пока пусто)"


def turn_prompts(s, c):
    st = s.settings
    memory = "\n".join("- " + x for x in c.memories) or "(пусто)"
    thoughts = "\n".join("- " + x for x in c.thoughts[-8:]) or "(пусто)"
    setting = st["setting"].strip() or "(не задано)"
    amap, place = s.world.map_text(), s.world.place_text(c.name)
    # Неизменная часть идёт первой, меняющаяся (место, мысли) — в конце: Ollama переиспользует
    # уже посчитанное начало запроса, и повторный ход того же персонажа считается быстрее.
    parts = [
        f"Ты играешь роль персонажа «{c.name}» в ролевой истории. Всегда оставайся в образе. Пиши по-русски.\n\n",
        "ПРАВИЛА:\n"
        "1. Ты знаешь только своё (лист, память, мысли, окружение) и то, что видел и слышал в журнале событий. "
        "Чужих мыслей и секретов ты не знаешь.\n"
        "2. Свои секреты и скрытые цели раскрывай, только если сам решишь, что это необходимо.\n"
        "3. Говори и действуй только за себя. Не пиши реплики других персонажей и не решай за них.\n"
        "4. Реплика короткая: 1–4 предложения. Действия пиши в *звёздочках*.\n"
        "5. Ответ — JSON с двумя полями: \"thoughts\" — твои скрытые мысли (1–2 предложения, их никто не слышит), "
        "\"words\" — то, что ты говоришь и делаешь вслух.\n\n",
        f"МИР И СИТУАЦИЯ:\n{setting}\n\n",
    ]
    if amap:
        parts.append(f"КАРТА БУНКЕРА (её знают все):\n{amap}\n\n")
    parts += [f"ЛИСТ ТВОЕГО ПЕРСОНАЖА (это знаешь только ты):\n{sheet_text(c)}\n\n",
              f"ТВОЯ ПАМЯТЬ:\n{memory}\n\n"]
    if place:
        parts.append(f"ГДЕ ТЫ СЕЙЧАС:\n{place}\n\n")
    parts.append(f"ТВОИ НЕДАВНИЕ ЛИЧНЫЕ МЫСЛИ:\n{thoughts}")
    log = "\n".join(fmt_msg(m) for m in recent_log(s, c.name, st["history"])) or "(пока ничего не произошло)"
    user = f"ЖУРНАЛ СОБЫТИЙ:\n{log}\n\nТвой ход, {c.name}. Ответь JSON."
    return "".join(parts), user


# ---------------------------------------------------------------- разбор ответа

def _grab(buf, key):
    """Достать значение строкового поля из JSON, даже если он оборван на полуслове."""
    m = re.search(r'"%s"\s*:\s*"' % key, buf)
    if not m:
        return ""
    out, i = [], m.end()
    while i < len(buf):
        ch = buf[i]
        if ch == "\\":
            if i + 1 >= len(buf):
                break
            n = buf[i + 1]
            if n == "u":
                if i + 6 > len(buf):
                    break
                try:
                    out.append(chr(int(buf[i + 2:i + 6], 16)))
                except ValueError:
                    pass
                i += 6
                continue
            out.append({"n": "\n", "t": "\t"}.get(n, n))
            i += 2
            continue
        if ch == '"':
            break
        out.append(ch)
        i += 1
    return "".join(out).encode("utf-16", "surrogatepass").decode("utf-16", "replace")


def parse_partial(buf):
    return _grab(buf, "thoughts"), _grab(buf, "words")


def parse_turn(raw):
    raw = strip_think(raw)
    try:
        d = json.loads(raw)
        thoughts, words = str(d.get("thoughts", "")), str(d.get("words", ""))
    except (ValueError, AttributeError):
        thoughts, words = parse_partial(raw)
        if not thoughts and not words:
            words = raw
    return thoughts.strip(), words.strip()


# ---------------------------------------------------------------- движок

class Engine:
    def __init__(self, client):
        self.client = client

    @staticmethod
    def _opts(st, num_predict):
        return {"num_ctx": int(st["num_ctx"]), "temperature": float(st["temperature"]),
                "num_predict": num_predict}

    def _use(self, st, model, status):
        status(f"Подготовка модели {model}…")
        if st.get("unload_others"):
            self.client.only_loaded(model)

    # ----- реплика персонажа

    def turn(self, s, c, stop, status=_noop, on_text=None):
        st = s.settings
        if not c.model:
            raise EngineError(f"У персонажа «{c.name}» не выбрана модель: нажмите «Править» и укажите её.")
        self._use(st, c.model, status)
        status(f"{c.name} отвечает…  (модель {c.model})")
        system, user = turn_prompts(s, c)
        buf = []

        def on_token(piece):
            buf.append(piece)
            if on_text:
                on_text(*parse_partial("".join(buf)))

        raw = self.client.chat(c.model, [{"role": "system", "content": system},
                                         {"role": "user", "content": user}],
                               options=self._opts(st, 450), fmt=TURN_SCHEMA,
                               on_token=on_token, stop=stop)
        if stop.is_set():
            return None
        thoughts, words = parse_turn(raw)
        m = s.add_message("say", words or "*молчит*", speaker=c.name, thought=thoughts)
        if thoughts:
            c.thoughts.append(thoughts)
        if st.get("auto_sheet"):
            try:
                self._auto_sheet(s, c, m)
            except (OllamaError, ValueError, AttributeError, TypeError) as e:
                status(f"Лист не обновлён: {e}")
        c.save()
        return m

    def _auto_sheet(self, s, c, m):
        """Нейросеть сама правит базовое: инвентарь и состояние. Изменения пишутся в m['delta'] для отката."""
        inv = c.sheet.setdefault("inventory", [])
        events = "\n".join(fmt_msg(x) for x in recent_log(s, c.name, 6))
        prompt = (
            f"Персонаж: {c.name}.\n"
            f"Инвентарь сейчас: {'; '.join(inv) or 'пусто'}.\n"
            f"Состояние сейчас: {c.sheet.get('state') or 'не указано'}.\n\n"
            f"Последние события:\n{events}\n\n"
            "Определи, что изменилось у этого персонажа именно из этих событий.\n"
            "inventory_add — предметы, которые он взял или получил.\n"
            "inventory_remove — предметы, которые он потерял, отдал или израсходовал.\n"
            "state — его состояние одной короткой фразой (здоровье, самочувствие); если не изменилось, верни прежнее.\n"
            "Ничего не выдумывай. Если изменений нет, оставь списки пустыми."
        )
        raw = self.client.chat(c.model, [{"role": "user", "content": prompt}],
                               options=self._opts(s.settings, 200), fmt=UPDATE_SCHEMA, stream=False)
        d = json.loads(strip_think(raw))
        delta = {"add": [], "remove": []}
        have = {x.lower() for x in inv}
        for it in d.get("inventory_add") or []:
            it = str(it).strip()
            if it and it.lower() not in have:
                inv.append(it)
                have.add(it.lower())
                delta["add"].append(it)
        for it in d.get("inventory_remove") or []:
            low = str(it).strip().lower()
            for x in list(inv):
                xl = x.lower()
                if low and (low == xl or (len(low) > 2 and low in xl) or (len(xl) > 2 and xl in low)):
                    inv.remove(x)
                    delta["remove"].append(x)
                    break
        new = str(d.get("state") or "").strip()
        if new and new != c.sheet.get("state"):
            delta["state_before"] = c.sheet.get("state", "")
            delta["state"] = new
            c.sheet["state"] = new
        if delta["add"] or delta["remove"] or "state" in delta:
            m["delta"] = delta
            s.save_log()

    # ----- режиссёр

    def pick_speaker(self, s, stop, status=_noop):
        """Режиссёр выбирает, кто говорит дальше. Возвращает (имя, причина)."""
        names = list(s.order)
        if not names:
            raise EngineError("В сессии пока нет персонажей.")
        if len(names) == 1:
            return names[0], ""
        st = s.settings
        if st.get("director_mode", "rules") == "rules":
            return rules_pick(s)
        if not st.get("director_model"):
            raise EngineError("Выберите модель режиссёра: Сессия → Настройки сессии.")
        self._use(st, st["director_model"], status)
        status("Режиссёр решает, кто говорит дальше…")
        cast = "\n".join(
            "- " + n + (f" ({s.characters[n].sheet['profession']})" if s.characters[n].sheet.get("profession") else "")
            + (f", сейчас: {s.world.where(n)['name']}" if s.world.where(n) else "") for n in names)
        public = "\n".join(fmt_msg(m) for m in [m for m in s.log if not m.get("audience")][-15:])
        note = st.get("director_note", "").strip()
        prompt = (
            "Ты — режиссёр сцены. Реши, кто из персонажей должен говорить следующим, "
            "чтобы история развивалась логично и интересно.\n\n"
            f"Мир и ситуация: {st['setting'].strip() or '(не задано)'}\n\n"
            f"Персонажи:\n{cast}\n\n"
            f"Последние события:\n{public or '(пока ничего)'}\n\n"
            f"Последним говорил: {last_speaker(s) or 'никто'}. Не давай слово одному и тому же персонажу подряд "
            "без причины; давай его тому, к кому обратились, кого обвинили или кто давно молчит.\n"
            + (f"Указание автора: {note}\n" if note else "")
            + "Ответь JSON: reason — одно короткое предложение, speaker — имя персонажа."
        )
        schema = {"type": "object",
                  "properties": {"reason": {"type": "string"}, "speaker": {"type": "string", "enum": names}},
                  "required": ["reason", "speaker"]}
        raw = self.client.chat(st["director_model"], [{"role": "user", "content": prompt}],
                               options=self._opts(st, 120), fmt=schema, stream=False)
        try:
            d = json.loads(strip_think(raw))
        except ValueError:
            d = {}
        name = d.get("speaker") if isinstance(d, dict) else None
        if name not in names:
            return next_in_order(s)
        return name, str(d.get("reason", "")).strip()

    # ----- сценарист: вопросы, варианты сюжета, правка плана, проверка и обновление мира

    def _writer(self, s, status):
        model = writer_model(s)
        if not model:
            raise EngineError("Выберите модель сценариста: Сессия → Настройки сессии.")
        self._use(s.settings, model, status)
        return model

    def _writer_context(self, s, n=30):
        events = writer_events(s, n=n, thoughts=s.settings.get("writer_sees_thoughts", True))
        return writer_system(s) + f"\n\nЖУРНАЛ СОБЫТИЙ (последние {n}, включая тайные сообщения):\n{events}"

    def ask_writer(self, s, history, question, stop, status=_noop, on_token=None):
        """Вопрос автора сценаристу. history — список (роль, текст) прошлой переписки; ответ идёт потоком."""
        model = self._writer(s, status)
        msgs = [{"role": "system", "content": self._writer_context(s)}]
        msgs += [{"role": "user" if r == "user" else "assistant", "content": t} for r, t in history[-8:]]
        msgs.append({"role": "user", "content": question})
        status("Сценарист отвечает…")
        return strip_think(self.client.chat(model, msgs, options=self._opts(s.settings, 700),
                                            on_token=on_token, stop=stop))

    def check_world(self, s, stop, status=_noop, on_token=None):
        model = self._writer(s, status)
        task = ("Проверь мир на противоречия: план, положение персонажей, состояние объектов, журнал и правда автора. "
                "Ищи: персонаж оказался там, куда нет прохода; предмет сразу в двух местах; сломанное работает; "
                "персонаж знает то, чего знать не мог; действие противоречит описанию комнаты. "
                "Перечисли не больше пяти найденных проблем, по строке на каждую. "
                "Если противоречий нет, так и скажи одной фразой.")
        status("Сценарист проверяет мир…")
        return strip_think(self.client.chat(
            model, [{"role": "system", "content": self._writer_context(s, 40)}, {"role": "user", "content": task}],
            options=self._opts(s.settings, 500), on_token=on_token, stop=stop))

    def story_options(self, s, hint="", task_id=0, stop=None, status=_noop):
        """Три варианта следующего шага: сцена для всех и, если нужно, тайное задание. Выбирает и правит автор."""
        model = self._writer(s, status)
        names = list(s.characters)
        schema = copy.deepcopy(OPTIONS_SCHEMA)
        if names:
            schema["properties"]["options"]["items"]["properties"]["secret_to"]["items"] = {
                "type": "string", "enum": names}
        task = ("Предложи 3 разных варианта следующего шага сюжета.\n"
                "title — короткое название (3–6 слов).\n"
                "scene — текст сцены, который автор покажет ВСЕМ персонажам: 2–4 предложения о том, что происходит "
                "вокруг. Не решай за персонажей, что они говорят и делают.\n"
                "secret_to и secret — необязательное тайное задание одному или нескольким персонажам "
                "(имена из списка персонажей). Если не нужно — пустой список и пустая строка.\n"
                "task — номер задачи автора, которой помогает вариант, или 0.\n"
                + (f"Пожелание автора: {hint}\n" if hint else ""))
        status("Сценарист придумывает варианты…")
        raw = self.client.chat(model, [{"role": "system", "content": self._writer_context(s, 25)},
                                       {"role": "user", "content": task}],
                               options=self._opts(s.settings, 900), fmt=schema, stream=False)
        out = []
        for o in parse_json(raw).get("options") or []:
            scene = str(o.get("scene") or "").strip()
            if not scene:
                continue
            to = [n for n in (o.get("secret_to") or []) if n in s.characters]
            out.append({"title": str(o.get("title") or "").strip() or "Вариант", "scene": scene, "secret_to": to,
                        "secret": str(o.get("secret") or "").strip() if to else "",
                        "task": int(o.get("task") or 0)})
        if not out:
            raise EngineError("Сценарист не предложил вариантов. Повторите запрос.")
        return out[:4]

    def plan_edit(self, s, request, stop=None, status=_noop):
        """Правка плана обычными словами → список операций, которые автор подтверждает. Возвращает (кратко, операции)."""
        model = self._writer(s, status)
        task = ("Автор просит изменить план бункера обычными словами. Преврати просьбу в список операций.\n"
                "Операции и поля:\n"
                "- add_room: room = название новой комнаты, text = описание\n"
                "- add_object: room = комната, object = название объекта, text = описание\n"
                "- remove_object: room, object\n"
                "- remove_room: room\n"
                "- set_state: room, object, text = новое состояние (например «сломан», «протекает»)\n"
                "- describe: room, object (пусто — описание самой комнаты), text = новое описание\n"
                "- link: room = комната A, target = комната B, text = название прохода (дверь, люк…)\n"
                "- unlink: room, target\n"
                "- set_visibility: room, object, text = «all» (видят все), «author» (только автор) или имена через запятую\n"
                "- move: object = имя персонажа, target = комната назначения\n"
                "Неиспользуемые поля оставляй пустыми строками. Названия существующих комнат и объектов бери "
                "точно из плана. Делай только то, о чём просит автор. summary — одно предложение: что именно ты сделаешь.\n\n"
                f"Просьба автора: {request}")
        status("Сценарист готовит изменения плана…")
        raw = self.client.chat(model, [{"role": "system", "content": writer_system(s)},
                                       {"role": "user", "content": task}],
                               options=self._opts(s.settings, 700), fmt=OPS_SCHEMA, stream=False)
        d = parse_json(raw)
        ops = [{k: (op["op"] if k == "op" else str(op.get(k) or "").strip())
                for k in ("op", "room", "object", "target", "text")}
               for op in d.get("ops") or [] if op.get("op") in OPS]
        return str(d.get("summary") or "").strip(), ops

    def sync_world(self, s, stop=None, status=_noop):
        """Сценарист читает новые события и предлагает, кто куда перешёл и что изменилось в объектах.
        Ничего не применяет: изменения накладывает приложение. None — если обновлять нечего."""
        w = s.world
        start = w.synced_upto
        if start >= len(s.log) or not w.rooms or not s.characters:
            return None
        model = self._writer(s, status)
        status("Сценарист обновляет мир…")
        names, rooms = list(s.characters), [r["name"] for r in w.rooms]
        objs = sorted({o["name"] for o in w.objects})
        props = {
            "moves": {"type": "array", "items": {"type": "object", "properties": {
                "who": {"type": "string", "enum": names}, "room": {"type": "string", "enum": rooms}},
                "required": ["who", "room"]}},
            "done_tasks": {"type": "array", "items": {"type": "integer"}},
            "note": {"type": "string"},
        }
        required = ["moves", "done_tasks", "note"]
        if objs:
            props["states"] = {"type": "array", "items": {"type": "object", "properties": {
                "room": {"type": "string", "enum": rooms}, "object": {"type": "string", "enum": objs},
                "state": {"type": "string"}}, "required": ["room", "object", "state"]}}
            required.append("states")
        task = ("Обнови мир по новым событиям. Записывай ТОЛЬКО то, что действительно произошло "
                "(не намерения и не мысли).\n"
                "moves — кто куда перешёл: who — персонаж, room — комната, где он оказался к концу событий.\n"
                "states — объекты, чьё состояние изменилось: room, object, state — новое короткое состояние.\n"
                "done_tasks — номера задач автора, которые явно выполнены в этих событиях.\n"
                "note — одно предложение автору, если заметил несостыковку, иначе пустая строка.\n"
                "Если изменений нет, оставь списки пустыми.\n\n"
                "НОВЫЕ СОБЫТИЯ:\n" + writer_events(s, start=start, n=40, thoughts=False))
        raw = self.client.chat(model, [{"role": "system", "content": writer_system(s)},
                                       {"role": "user", "content": task}],
                               options=self._opts(s.settings, 500),
                               fmt={"type": "object", "properties": props, "required": required}, stream=False)
        d = parse_json(raw)
        return {"moves": d.get("moves") or [], "states": d.get("states") or [],
                "done_tasks": [int(x) for x in d.get("done_tasks") or []],
                "note": str(d.get("note") or "").strip(), "upto": len(s.log)}

    # ----- итоги сцены → память (только предложение, принимает пользователь)

    def propose_memories(self, s, stop, status=_noop):
        st = s.settings
        model = self._writer(s, status)
        out = {}
        for c in s.characters.values():
            if stop.is_set():
                break
            fresh = s.log[c.summarized_upto:]
            seen = [m for m in fresh if can_see(m, c.name)][-60:]
            if not seen:
                continue
            status(f"Хронист пишет память для {c.name}…")
            own = "\n".join("- " + m["thought"] for m in fresh if m.get("speaker") == c.name and m.get("thought"))
            old = "\n".join("- " + x for x in c.memories[-10:]) or "(пусто)"
            prompt = (
                f"Ты ведёшь внутреннюю память персонажа «{c.name}». Ниже — новые события, которые он видел, "
                "и его личные мысли за это время.\n"
                "Напиши от первого лица («я») от 1 до 4 коротких пунктов памяти: что произошло, что он понял, "
                "кому стал больше или меньше доверять, какие у него теперь планы. "
                "Только то, что персонаж мог знать сам. Один пункт — одна строка, без нумерации. "
                "Не повторяй то, что уже есть в памяти.\n\n"
                f"УЖЕ В ПАМЯТИ:\n{old}\n\nСОБЫТИЯ:\n" + "\n".join(fmt_msg(m) for m in seen)
                + f"\n\nЕГО ЛИЧНЫЕ МЫСЛИ:\n{own or '(нет)'}"
            )
            raw = self.client.chat(model, [{"role": "user", "content": prompt}],
                                   options=self._opts(st, 400), stream=False)
            lines = [re.sub(r"^\s*(?:[-*•]|\d+[.)])\s*", "", ln).strip() for ln in strip_think(raw).splitlines()]
            lines = [ln for ln in lines if ln]
            if lines:
                out[c.name] = lines
        return out

    # ----- сценарий

    def export_script(self, s, model, with_thoughts, stop, status=_noop, chunk=18):
        """Оформить журнал в сценарий кусками. Возвращает (путь к файлу, текст)."""
        st = s.settings
        if not model:
            raise EngineError("Выберите модель-сценариста.")
        msgs = [m for m in s.log if m["kind"] in ("scene", "task", "say")]
        if not msgs:
            raise EngineError("В журнале пока нет сообщений для сценария.")
        self._use(st, model, status)
        cast = "\n".join("- " + n + (f", {s.characters[n].sheet['profession']}"
                                     if s.characters[n].sheet.get("profession") else "") for n in s.order)

        def line(m):
            if m["kind"] == "say":
                out = f"{m['speaker']}: {m['text']}"
                if with_thoughts and m.get("thought"):
                    out += f"\n{m['speaker']} (мысли, закадровый голос): {m['thought']}"
                return out
            return fmt_msg(m).replace("[СЦЕНА]", "[ОТ АВТОРА: сцена]").replace("[ЗАДАЧА]", "[ОТ АВТОРА: задача]")

        parts, tail = [], ""
        total = (len(msgs) + chunk - 1) // chunk
        for i in range(0, len(msgs), chunk):
            if stop.is_set():
                break
            status(f"Пишу сценарий: часть {i // chunk + 1} из {total}…")
            prompt = (
                "Ты — сценарист. Оформи фрагмент записи ролевой истории как сценарий видео на русском языке.\n\n"
                f"Мир: {st['setting'].strip() or '(не задано)'}\nПерсонажи:\n{cast}\n\n"
                "ПРАВИЛА ОФОРМЛЕНИЯ:\n"
                "- Новая сцена начинается заголовком: ИНТ. или НАТ. — МЕСТО — ВРЕМЯ СУТОК. "
                "Если место и время не менялись, продолжай сцену без заголовка.\n"
                "- Ремарки (действия, обстановка, звуки) — отдельными строками в круглых скобках, в настоящем времени.\n"
                "- Реплика: имя персонажа ЗАГЛАВНЫМИ на отдельной строке, ниже — текст реплики. "
                "Действия из *звёздочек* перенеси в ремарки.\n"
                "- Мысли персонажа оформляй как «ИМЯ (В.О.)», только если они есть в записи.\n"
                "- Не добавляй новых реплик и событий и не меняй смысл сказанного. Можно править пунктуацию "
                "и добавлять короткие ремарки.\n"
                "- Выведи только текст сценария, без пояснений.\n\n"
                + (f"КОНЕЦ УЖЕ НАПИСАННОГО (для связности):\n{tail}\n\n" if tail else "")
                + "ФРАГМЕНТ ЗАПИСИ:\n" + "\n".join(line(m) for m in msgs[i:i + chunk])
            )
            raw = self.client.chat(model, [{"role": "user", "content": prompt}],
                                   options=self._opts(st, 1800), stream=False)
            text = strip_think(raw)
            if text:
                parts.append(text)
                tail = text[-700:]
        if stop.is_set():
            return None, ""
        if not parts:
            return None, ""
        head = (f"{s.name.upper()}\nСценарий (черновик, написан нейросетью {model})\n\nПЕРСОНАЖИ:\n{cast}\n\n"
                + "=" * 40 + "\n\n")
        script = head + "\n\n".join(parts) + "\n"
        folder = os.path.join(s.path, "exports")
        os.makedirs(folder, exist_ok=True)
        base = os.path.join(folder, f"script_{datetime.now():%Y%m%d_%H%M%S}")
        path, n = base + ".txt", 1
        while os.path.exists(path):  # два экспорта в одну секунду не должны затирать друг друга
            n += 1
            path = f"{base}_{n}.txt"
        with open(path, "w", encoding="utf-8") as f:
            f.write(script)
        return path, script
