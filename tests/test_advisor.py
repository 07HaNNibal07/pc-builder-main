"""Тесты подбора и модели FPS. Запуск: python -m unittest discover tests"""
import re
import unittest

from collector import advisor, perf

try:
    CAT = advisor.load()
except FileNotFoundError:          # каталог не собран — проверки на реальных данных пропускаются
    CAT = None

SCENARIOS = [(b, t, r) for t, rs in (("game", (0, 1, 2)), ("work", (1,)), ("office", (1,)))
             for r in rs for b in (40000, 70000, 120000, 200000, 350000)]


class Perf(unittest.TestCase):
    def test_reference_is_100(self):
        self.assertEqual(perf.gpu_score("RTX 4060"), 100)
        self.assertEqual(perf.cpu_score("Ryzen 5 5600"), (100, 100))

    def test_fps_monotonic(self):
        """Сильнее видеокарта или процессор — FPS не меньше."""
        for gm in perf.GAMES:
            for res in range(3):
                f = lambda g, c: perf.fps(gm, res, g, 16, c)["fps"]
                self.assertLessEqual(f(100, 100), f(150, 100))
                self.assertLessEqual(f(100, 100), f(100, 150))

    def test_bottleneck(self):
        """Слабая карта + сильный процессор — упор в карту, и наоборот."""
        cs2 = perf.GAMES[0]
        self.assertEqual(perf.fps(cs2, 2, 40, 8, 170)["limit"], "gpu")
        self.assertEqual(perf.fps(cs2, 0, 300, 16, 40)["limit"], "cpu")

    def test_vram_penalty(self):
        cp = next(g for g in perf.GAMES if g["id"] == "cyberpunk")
        self.assertLess(perf.fps(cp, 2, 150, 8, 150)["fps"], perf.fps(cp, 2, 150, 16, 150)["fps"])

    def test_ddr4_penalty(self):
        """DDR4 снижает упор в процессор только там, где баллы сняты на DDR5."""
        self.assertLess(perf.mem_k("LGA1700", "DDR4"), 1)
        self.assertEqual(perf.mem_k("LGA1700", "DDR5"), 1)
        self.assertEqual(perf.mem_k("AM4", "DDR4"), 1)
        cs2 = perf.GAMES[0]
        self.assertLess(perf.fps(cs2, 0, 300, 16, 138, memk=perf.mem_k("LGA1700", "DDR4"))["fps"],
                        perf.fps(cs2, 0, 300, 16, 138)["fps"])

    def test_old_cpu_below_modern(self):
        self.assertLess(perf.cpu_score("Core i7-4790K")[0], perf.cpu_score("Core i3-12100F")[0])
        self.assertGreater(perf.cpu_score("Xeon E5-2698 v3")[1], perf.cpu_score("Core i7-4790K")[1])


class Check(unittest.TestCase):
    BUILD = {"cpu": {"model": "Ryzen 5 7500F", "socket": "AM5", "tdp": 65, "igpu": False},
             "mb": {"socket": "AM5", "mem": "DDR5", "form": "mATX", "memSlots": 2, "m2": 2},
             "ram": {"type": "DDR5", "cap": 32, "kit": 2},
             "gpu": {"len": 280, "slots": 2, "power": 200, "psu": 650},
             "cooler": {"h": 150, "type": "tower", "towers": 1},
             "case": {"tpl": "tower", "forms": ["ATX", "mATX", "ITX"], "maxGpu": 330, "maxCooler": 160},
             "psu": {"watt": 650, "form": "ATX"}, "ssd": {"form": "M.2"}}

    def broken(self, **patch):
        b = {k: dict(v) for k, v in self.BUILD.items()}
        for key, val in patch.items():
            part, field = key.split("__")
            b[part][field] = val
        return advisor.check(b)

    def test_ok(self):
        self.assertEqual(advisor.check(self.BUILD), [])

    def test_conflicts(self):
        self.assertTrue(self.broken(cpu__socket="AM4"))
        self.assertTrue(self.broken(ram__type="DDR4"))
        self.assertTrue(self.broken(ram__kit=4))
        self.assertTrue(self.broken(gpu__len=340))
        self.assertTrue(self.broken(cooler__h=170))
        self.assertTrue(self.broken(psu__watt=550))
        self.assertTrue(self.broken(case__forms=["ITX"]))

    def test_no_gpu_needs_igpu(self):
        b = {**self.BUILD, "gpu": None}
        self.assertTrue(advisor.check(b))
        self.assertEqual(advisor.check({**b, "cpu": {**b["cpu"], "igpu": True}}), [])


@unittest.skipUnless(CAT, "нет catalog.js")
class Advise(unittest.TestCase):
    def test_variants_valid(self):
        """Каждый вариант совместим, «оптимальная» укладывается в бюджет."""
        for budget, task, res in SCENARIOS:
            with self.subTest(budget=budget, task=task, res=res):
                r = advisor.advise(CAT, budget, task, res)
                for v in r["variants"]:
                    self.assertEqual(v["problems"], [], v["label"])
                    self.assertEqual(v["total"], sum(x["price"] for x in v["parts"].values()))
                    if v["label"] != "С запасом":
                        self.assertLessEqual(v["total"], budget)
                    if task == "office":
                        self.assertNotIn("gpu", v["parts"])

    def test_more_money_not_worse(self):
        """Больше бюджет — полезность оптимальной сборки не падает."""
        for task, res in (("game", 1), ("work", 1)):
            scores = [next(v["score"] for v in advisor.advise(CAT, b, task, res)["variants"]
                           if v["label"] == "Оптимальная") for b in (60000, 90000, 130000, 200000, 300000)]
            self.assertEqual(scores, sorted(scores), task)

    def test_preferences(self):
        r = advisor.advise(CAT, 150000, "game", 1, cpu_vendor="Intel", gpu_vendor="AMD", max_form="mATX")
        for v in r["variants"]:
            self.assertEqual(v["parts"]["cpu"]["vendor"], "Intel")
            self.assertEqual(advisor.chip_vendor(v["parts"]["gpu"]["chip"]), "AMD")
            self.assertIn(v["parts"]["mb"]["form"], ("mATX", "ITX"))

    def test_upgrade(self):
        """Доплата ведёт за бюджет, и подбор с её бюджетом даёт не меньший прирост."""
        for budget, task, res in SCENARIOS:
            r = advisor.advise(CAT, budget, task, res)
            for v in r["variants"]:
                u = v["upgrade"]
                if v["label"] == "Экономная":
                    self.assertIsNone(u)
                if not u:
                    continue
                with self.subTest(budget=budget, task=task, label=v["label"]):
                    self.assertGreater(u["budget"], budget)
                    self.assertEqual(u["delta"], u["budget"] - v["total"])
                    best = next(x for x in advisor.advise(CAT, u["budget"], task, res)["variants"]
                                if x["label"] == "Оптимальная")
                    # gain округлён до целого процента (фактический бывает до 0,5 п. п. меньше),
                    # score — до 0,1: при баллах ~4 (бюджет 40 000) это больше процента
                    self.assertGreaterEqual(best["score"], v["score"] * (1 + (u["gain"] - 0.5) / 100) - 0.1)

    def test_quality_upgrades(self):
        """Улучшения «не про FPS»: в пределах суммы, по одному на вид, сборка остаётся совместимой."""
        for budget in (70000, 120000):
            v = advisor.advise(CAT, budget, "game", 1)["variants"][0]
            ids = {k: x["id"] for k, x in v["parts"].items()}
            for resale in (False, True):
                ex = advisor.upgrade_build(CAT, ids, 60000, "game", 1, resale=resale)["extras"]
                with self.subTest(budget=budget, resale=resale):
                    self.assertTrue(ex)
                    self.assertEqual(len({s["kind"] for s in ex}), len(ex))
                    for s in ex:
                        self.assertLessEqual(s["cost"], 60000)
                        self.assertNotEqual(s["ids"], ids)
                        self.assertEqual(advisor.check({**advisor.resolve(CAT, s["ids"]), "ram_qty": 1}), [])

    def test_ddr5_upgrade(self):
        """Сборка на LGA1700 + DDR4: переход на DDR5 предлагается ровно в одной группе."""
        v = advisor.advise(CAT, 150000, "game", 1, cpu_vendor="Intel")["variants"][-1]
        P = v["parts"]
        if P["cpu"]["socket"] != "LGA1700":
            self.skipTest("оптимальная Intel-сборка не на LGA1700")
        adv = advisor.Advisor(CAT, 10 ** 7)
        mb = advisor.cheapest(adv.mb, lambda m: m["socket"] == "LGA1700" and m["mem"] == "DDR4"
                              and m["form"] in P["case"]["forms"])
        ram = advisor.cheapest(adv.ram, lambda r: r["type"] == "DDR4" and r["cap"] >= 16 and (r.get("kit") or 1) == 2)
        ids = {**{k: x["id"] for k, x in P.items()}, "mb": mb["id"], "ram": ram["id"]}
        for res in (0, 1):
            r = advisor.upgrade_build(CAT, ids, 60000, "game", res)
            in_fps = any("DDR5" in s["title"] for s in r["suggestions"])
            in_extras = any(s["kind"] == "ddr5" for s in r.get("extras", []))
            with self.subTest(res=res, fps=in_fps):
                self.assertTrue(in_fps or in_extras)
                self.assertFalse(in_fps and in_extras)

    def test_extra_drives(self):
        """Дополнительные накопители: входят в цену; лишние — конфликт по местам; список для добавления есть."""
        v = advisor.advise(CAT, 120000, "game", 1)["variants"][0]
        ids = {k: x["id"] for k, x in v["parts"].items()}
        base = advisor.build_info(CAT, ids)
        hdd = min((x for x in CAT["hdd"] if x.get("price")), key=lambda x: x["price"])
        one = advisor.build_info(CAT, ids, drives=[hdd["id"]])
        self.assertEqual(one["total"], base["total"] + hdd["price"])
        self.assertEqual([d["id"] for d in one["drives"]], [hdd["id"]])
        m2 = next(x for x in CAT["ssd"] if x.get("price") and (x.get("form") or "").startswith("M.2"))
        many = advisor.build_info(CAT, ids, drives=[m2["id"]] * 6)
        self.assertTrue(any(c["part"] == "ssd" and not c["ok"] for c in many["checks"]))
        self.assertTrue(base["drive_room"])
        self.assertTrue(many["drive_room"])          # слоты M.2 заняты, но места 2,5″/3,5″ свободны
        s25 = next(x for x in CAT["ssd"] if x.get("price") and x.get("form") == "2.5")
        full = advisor.build_info(CAT, ids, drives=[m2["id"]] * 6 + [s25["id"]] * 6 + [hdd["id"]] * 6)
        self.assertFalse(full["drive_room"])
        specs = " ".join(o["spec"] for o in advisor.options(CAT, ids, "drive", limit=1000)["items"])
        self.assertIn("HDD", specs)
        self.assertIn("M.2", specs)
        self.assertTrue(any(o["id"] == hdd["id"] for o in advisor.options(CAT, ids, "drive", q=hdd["name"])["items"]))
        with self.assertRaises(KeyError):
            advisor.build_info(CAT, ids, drives=[ids["cpu"]])

    def test_search_by_spec(self):
        """Поиск в списке замен находит по характеристикам, которых нет в названии: сокет, чипсет, тип."""
        v = advisor.advise(CAT, 120000, "game", 1)["variants"][0]
        ids = {k: x["id"] for k, x in v["parts"].items()}
        for part, q, field, want in (("cpu", "AM5", "socket", "AM5"), ("cpu", "lga 1700", "socket", "LGA1700"),
                                     ("mb", "am5", "socket", "AM5"), ("ram", "ddr-5", "type", "DDR5")):
            with self.subTest(part=part, q=q):
                items = advisor.options(CAT, ids, part, q=q, limit=1000)["items"]
                self.assertTrue(items)
                self.assertTrue(all(advisor.index(CAT)[o["id"]].get(field) == want for o in items))
        gpus = advisor.options(CAT, ids, "gpu", q="5070 ti", limit=1000)["items"]
        self.assertTrue(gpus)
        # «ti» — отдельно или сразу после числа («5070Ti»), но не внутри слова («Edition»)
        self.assertTrue(all(re.search(r"(?<![a-zа-я])ti", o["name"].lower()) for o in gpus))

    def test_options_sort(self):
        """Сортировка списка замен по цене: подходящие по возрастанию/убыванию, неподходящие — в конце."""
        v = advisor.advise(CAT, 120000, "game", 1)["variants"][0]
        ids = {k: x["id"] for k, x in v["parts"].items()}
        for part in ("gpu", "cpu", "mb", "case"):
            for sort, desc in (("asc", False), ("desc", True)):
                with self.subTest(part=part, sort=sort):
                    items = advisor.options(CAT, ids, part, sort=sort, limit=1000)["items"]
                    ok = [o["price"] for o in items if not o["problem"]]
                    self.assertEqual(ok, sorted(ok, reverse=desc))
                    self.assertEqual(ok, [o["price"] for o in items[:len(ok)]])    # сначала все подходящие
        top = advisor.options(CAT, ids, "gpu", q="5070", sort="desc", limit=1)["items"][0]
        self.assertEqual(top["price"], max(o["price"] for o in advisor.options(CAT, ids, "gpu", q="5070", limit=1000)["items"]
                                           if not o["problem"]))

    def test_options_ignore_unrelated_problem(self):
        """Слабый кулер под новый процессор не делает все платы «неподходящими»: плата под сокет — наверху."""
        v = advisor.advise(CAT, 120000, "game", 1)["variants"][0]
        ids = {k: x["id"] for k, x in v["parts"].items()}
        hot = max((x for x in CAT["cpu"] if x.get("price") and x["socket"] == "LGA1700"), key=advisor.cpu_tdp)
        weak = min((x for x in CAT["cooler"] if x.get("price")), key=lambda x: x.get("tdp") or 0)
        ids.update(cpu=hot["id"], cooler=weak["id"])
        items = advisor.options(CAT, ids, "mb", limit=1000)["items"]
        ok = [o for o in items if not o["problem"]]
        self.assertTrue(ok)
        self.assertTrue(all(o["spec"].startswith("LGA1700") for o in ok))

    def test_options_filters(self):
        """Фильтры списка замен: отобранное подходит под все отмеченные значения и диапазоны, счётчики честные."""
        v = advisor.advise(CAT, 120000, "game", 1)["variants"][0]
        ids = {k: x["id"] for k, x in v["parts"].items()}
        r = advisor.options(CAT, ids, "ram", limit=1000)
        kit = next(f for f in r["facets"] if f["key"] == "kit")
        two = next(x for x in kit["values"] if x["v"] == "2")
        flt = {"kit": ["2"], "price": [None, 30000]}
        items = advisor.options(CAT, ids, "ram", limit=1000, filters=flt)["items"]
        self.assertTrue(items)
        by_id = {x["id"]: x for x in CAT["ram"]}
        for o in items:
            self.assertEqual(by_id[o["id"]].get("kit"), 2)
            self.assertLessEqual(o["price"], 30000)
        self.assertEqual(len(advisor.options(CAT, ids, "ram", limit=1000, filters={"kit": ["2"]})["items"]), two["n"])
        fit = advisor.options(CAT, ids, "mb", limit=1000, fit_only=True)["items"]
        self.assertTrue(all(not o["problem"] or o["current"] for o in fit))

    def test_old_am4_chipset(self):
        """Ryzen 1000/2000 и старые APU не ставятся на A520/B550: подбор берёт плату на старом чипсете."""
        for m in ("Ryzen 5 2600", "Ryzen 5 3400G", "Athlon 200GE"):
            self.assertTrue(advisor.old_am4({"socket": "AM4", "model": m}), m)
        for m in ("Ryzen 5 3600", "Ryzen 5 5600G", "Ryzen 7 5800X3D"):
            self.assertFalse(advisor.old_am4({"socket": "AM4", "model": m}), m)
        for task in ("game", "work", "office"):
            for budget in (30000, 50000, 70000):
                for v in advisor.advise(CAT, budget, task, 1)["variants"]:
                    p, mb = v["parts"]["cpu"], v["parts"]["mb"]
                    if advisor.old_am4(p):
                        self.assertNotIn(mb.get("chipset"), advisor.NEW_AM4, (task, budget, p["model"]))

    def test_heavy_cpu_board(self):
        """Мощный процессор не ставится на плату начального уровня."""
        for budget in (150000, 250000, 400000):
            for v in advisor.advise(CAT, budget, "work", 1)["variants"]:
                if advisor.heavy_cpu(v["parts"]["cpu"]):
                    self.assertNotIn(v["parts"]["mb"].get("chipset"), advisor.ENTRY_CHIPSETS)


if __name__ == "__main__":
    unittest.main()
