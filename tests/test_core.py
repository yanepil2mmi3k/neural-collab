import json
import os
import tempfile
import threading
import unittest
from types import SimpleNamespace

from engine import Engine, EngineError, can_see, next_in_order, parse_json, parse_turn, rules_pick
from gui import App
from storage import Session
from world import World


class Value:
    def __init__(self):
        self.value = ""

    def set(self, value):
        self.value = value


class EngineTests(unittest.TestCase):
    def test_message_visibility(self):
        message = {"kind": "task", "speaker": "", "text": "секрет", "audience": ["Иван"]}
        self.assertTrue(can_see(message, "Иван"))
        self.assertFalse(can_see(message, "Мария"))
        message["speaker"] = "Мария"
        self.assertTrue(can_see(message, "Мария"))
        message.pop("audience")
        self.assertTrue(can_see(message, "Мария"))

    def test_turn_order_and_name_mention(self):
        session = SimpleNamespace(order=["Иван", "Мария"], characters={"Иван": None, "Мария": None},
                                  log=[], world=World())
        session.log.append({"kind": "say", "speaker": "Иван", "text": "Мария, что скажешь?"})
        self.assertEqual(next_in_order(session)[0], "Мария")
        self.assertEqual(rules_pick(session)[0], "Мария")

    def test_complete_and_partial_turn_json(self):
        self.assertEqual(parse_turn('{"thoughts":"Надо уйти","words":"Пойду"}'), ("Надо уйти", "Пойду"))
        self.assertEqual(parse_turn('{"thoughts":"Надо уйти","words":"Пойду'), ("Надо уйти", "Пойду"))
        with self.assertRaises(EngineError):
            parse_json("не JSON")


class WorldTests(unittest.TestCase):
    def test_object_visibility_and_place_text(self):
        world = World()
        room = world.add_room("Генераторная")
        world.add_object(room["id"], "Ящик")
        world.add_object(room["id"], "Тайник", visibility=["Иван"])
        world.set_position("Иван", room["id"])
        world.set_position("Мария", room["id"])
        self.assertNotIn("Тайник", world.place_text("Мария"))
        self.assertIn("Тайник", world.place_text("Иван"))

    def test_links_reachability_and_operations(self):
        world = World()
        a = world.add_room("А")
        b = world.add_room("Б")
        c = world.add_room("В")
        world.link(a["id"], b["id"], "дверь")
        self.assertTrue(world.reachable(a["id"], b["id"]))
        self.assertFalse(world.reachable(a["id"], c["id"]))
        done, skipped = world.apply_ops([
            {"op": "add_object", "room": "А", "object": "Насос", "text": "старый"},
            {"op": "set_state", "room": "А", "object": "Насос", "text": "протекает"},
            {"op": "set_visibility", "room": "А", "object": "Насос", "text": "Иван"},
            {"op": "move", "object": "Иван", "target": "Б"},
            {"op": "link", "room": "Б", "target": "В"},
        ], ["Иван"])
        self.assertEqual(len(done), 5)
        self.assertEqual(skipped, [])
        pump = world.find_object("А", "Насос")
        self.assertEqual(pump["state"], "протекает")
        self.assertEqual(pump["visibility"], ["Иван"])
        self.assertEqual(world.where("Иван")["name"], "Б")
        self.assertTrue(world.reachable(a["id"], c["id"]))


class StorageTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.session = Session.create(self.temp.name, "Тест")

    def tearDown(self):
        self.temp.cleanup()

    def test_rename_updates_all_references(self):
        self.session.add_character("Иван")
        room = self.session.world.add_room("Склад")
        obj = self.session.world.add_object(room["id"], "Тайник", visibility=["Иван"])
        self.session.world.set_position("Иван", room["id"])
        self.session.add_message("say", "Привет", speaker="Иван")
        self.session.add_message("task", "Иди на склад", audience=["Иван"])

        self.session.rename_character("Иван", "Пётр")

        self.assertIn("Пётр", self.session.characters)
        self.assertNotIn("Иван", self.session.characters)
        self.assertEqual(self.session.log[0]["speaker"], "Пётр")
        self.assertEqual(self.session.log[1]["audience"], ["Пётр"])
        self.assertEqual(self.session.world.where("Пётр")["id"], room["id"])
        self.assertEqual(obj["visibility"], ["Пётр"])
        self.assertTrue(World.visible_to(obj, "Пётр"))

        loaded = Session(self.session.path)
        loaded_obj = loaded.world.object_by_id(obj["id"])
        self.assertEqual(loaded_obj["visibility"], ["Пётр"])

    def test_undo_restores_sheet_and_thoughts(self):
        character = self.session.add_character("Иван")
        character.sheet["inventory"] = ["ключ"]
        character.sheet["state"] = "здоров"
        message = self.session.add_message(
            "say", "Беру фонарь", speaker="Иван", thought="Он пригодится")
        message["delta"] = {
            "add": ["фонарь"], "remove": ["ключ"],
            "state_before": "здоров", "state": "ранен",
        }
        character.thoughts.append(message["thought"])
        character.sheet["inventory"] = ["фонарь"]
        character.sheet["state"] = "ранен"

        self.session.pop_last()

        self.assertEqual(self.session.log, [])
        self.assertEqual(character.thoughts, [])
        self.assertEqual(character.sheet["inventory"], ["ключ"])
        self.assertEqual(character.sheet["state"], "здоров")

    def test_corrupted_json_is_backed_up_and_reported(self):
        character = self.session.add_character("Иван")
        paths = [os.path.join(self.session.path, "chat.json"),
                 os.path.join(character.folder, "memory.json")]
        for path in paths:
            with open(path, "w", encoding="utf-8") as file:
                file.write("{повреждено")

        loaded = Session(self.session.path)

        self.assertEqual(loaded.log, [])
        self.assertEqual(len(loaded.load_problems), 2)
        for path in paths:
            self.assertTrue(os.path.isfile(path + ".broken"))
            self.assertTrue(any(path in problem for problem in loaded.load_problems))

    def test_missing_new_files_are_not_problems(self):
        self.assertEqual(self.session.load_problems, [])


class ModelRefreshTests(unittest.TestCase):
    def test_waiter_runs_after_current_refresh(self):
        app = App.__new__(App)
        app.models_loading = True
        app.models_waiters = []
        app.models_quiet = True
        app.models = []
        app.status = Value()
        opened = []

        App.refresh_models(app, done=lambda: opened.append(True))
        self.assertEqual(len(app.models_waiters), 1)
        App._models_done(app, ["model:latest"], None)

        self.assertEqual(opened, [True])
        self.assertEqual(app.models, ["model:latest"])
        self.assertFalse(app.models_loading)


class CancellationTests(unittest.TestCase):
    def make_session(self, root):
        session = Session.create(root, "Экспорт")
        character = session.add_character("Иван")
        character.model = "test-model"
        session.add_message("say", "Привет", speaker="Иван")
        return session

    def test_stopped_job_does_not_apply_result(self):
        app = App.__new__(App)
        app.set_busy = lambda _busy: None
        app.live = Value()
        app.writer = SimpleNamespace(live=Value())
        app.active = "Иван"
        app.refresh_queue = lambda: None
        app.status = Value()
        completed = []

        App._finish(app, completed.append, {"partial": True}, None, True)

        self.assertEqual(completed, [])
        self.assertEqual(app.status.value, "Остановлено")
        self.assertIsNone(app.active)

    def test_successful_job_applies_result(self):
        app = App.__new__(App)
        app.set_busy = lambda _busy: None
        app.live = Value()
        app.writer = SimpleNamespace(live=Value())
        app.active = None
        app.refresh_queue = lambda: None
        app.status = Value()
        completed = []

        App._finish(app, completed.append, "готово", None, False)

        self.assertEqual(completed, ["готово"])
        self.assertEqual(app.status.value, "Готово")

    def test_stopped_export_does_not_create_file(self):
        stop = threading.Event()

        class Client:
            def chat(self, *_args, **_kwargs):
                stop.set()
                return "Неполный черновик"

        with tempfile.TemporaryDirectory() as root:
            session = self.make_session(root)
            path, text = Engine(Client()).export_script(session, "test-model", False, stop)
            self.assertIsNone(path)
            self.assertEqual(text, "")
            self.assertFalse(os.path.exists(os.path.join(session.path, "exports")))

    def test_completed_export_creates_file(self):
        class Client:
            def chat(self, *_args, **_kwargs):
                return "ИНТ. БУНКЕР — ДЕНЬ"

        with tempfile.TemporaryDirectory() as root:
            session = self.make_session(root)
            path, text = Engine(Client()).export_script(session, "test-model", False, threading.Event())
            self.assertTrue(os.path.isfile(path))
            self.assertIn("ИНТ. БУНКЕР", text)
            with open(path, encoding="utf-8") as file:
                self.assertEqual(json.dumps(file.read(), ensure_ascii=False), json.dumps(text, ensure_ascii=False))


if __name__ == "__main__":
    unittest.main()
