"""HTTP-интерфейс системы: подбор, сводка сборки, замена деталей, улучшение сборки.
С того же адреса раздаётся веб-часть: web/index.html (интерфейс) и web/prototype-3d (3D-модель).

Запуск: docker compose up -d api → http://localhost:8792 (документация — /docs)
Каталог — web/prototype-3d/catalog.js (собирает collector/catalog.py); после пересборки
перечитывается сам, перезапуск не нужен.
"""
from __future__ import annotations

from pathlib import Path
from typing import Literal

from fastapi import FastAPI, HTTPException
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from collector import advisor, perf
from api.ai.router import router as ai_router

WEB = Path(__file__).resolve().parent.parent / "web"

app = FastAPI(title="ПК-Подбор", version="0.2",
              description="СППР при выборе конфигурации ВТ на основе агрегации данных торговых площадок")
app.include_router(ai_router)

_cache = {"mtime": None, "data": None}

Task = Literal["game", "work", "office"]
Res = Literal["1080p", "1440p", "4K"]
Part = Literal["cpu", "gpu", "mb", "ram", "ssd", "cooler", "case", "psu", "drive"]
Sort = Literal["fit", "asc", "desc"]


def catalog() -> dict:
    """Каталог в памяти; перечитывается, если файл обновился."""
    m = advisor.CATALOG.stat().st_mtime
    if m != _cache["mtime"]:
        _cache.update(mtime=m, data=advisor.load())
    return _cache["data"]


def run(fn, *args, **kw):
    """Неизвестная деталь в сборке — 404 с понятным текстом, а не 500."""
    try:
        return fn(catalog(), *args, **kw)
    except KeyError as e:
        raise HTTPException(404, str(e.args[0]))


class AdviseIn(BaseModel):
    budget: int = Field(ge=15000, le=2_000_000, description="Бюджет, ₽")
    task: Task = "game"
    res: Res = "1440p"
    cpu: Literal["AMD", "Intel"] | None = None
    gpu: Literal["NVIDIA", "AMD", "Intel"] | None = None
    form: Literal["ITX", "mATX", "ATX"] | None = Field(None, description="Наибольший форм-фактор платы")


class BuildIn(BaseModel):
    ids: dict[str, str] = Field(description="Детали сборки: {cpu, gpu, mb, ram, ssd, cooler, case, psu} → id каталога")
    ram_qty: int = Field(1, ge=1, le=8, description="Сколько комплектов памяти")
    drives: list[str] = Field(default_factory=list, max_length=12,
                              description="Дополнительные накопители (SSD, HDD) — id каталога; основной — ids.ssd")


class OptionsIn(BuildIn):
    part: Part = Field(description="Деталь; drive — дополнительный накопитель (SSD и HDD вместе)")
    drive: int | None = Field(None, ge=0, le=5, description="part=drive: какой из drives заменить; без него — добавить")
    q: str = Field("", max_length=100, description="Поиск по названию")
    sort: Sort = Field("fit", description="fit — текущая и подходящие первыми; asc/desc — по цене вверх/вниз")
    limit: int = Field(80, ge=1, le=300)
    filters: dict[str, list[str | float | None]] = Field(
        {}, description="Фильтры: {ключ: [значения]} или {ключ: [от, до]}; ключи и значения — из facets ответа")
    fit_only: bool = Field(False, description="Только подходящие к сборке")


class UpgradeIn(BuildIn):
    money: int = Field(ge=1000, le=1_000_000, description="Сколько готовы доплатить, ₽")
    task: Task = "game"
    res: Res = "1440p"
    resale: bool = Field(False, description="Учесть продажу старых деталей (≈ 50 % цены)")


@app.post("/api/advise")
def advise(q: AdviseIn) -> dict:
    """Варианты сборки под бюджет и задачу: детали, цена, FPS, объяснение выбора, доплата."""
    return run(advisor.advise, q.budget, q.task, perf.RES.index(q.res),
               cpu_vendor=q.cpu, gpu_vendor=q.gpu, max_form=q.form)


@app.post("/api/build")
def build(q: BuildIn) -> dict:
    """Сводка сборки: детали, итоговая цена, проверки совместимости, FPS во всех разрешениях."""
    return run(advisor.build_info, q.ids, q.ram_qty, q.drives)


@app.post("/api/options")
def options(q: OptionsIn) -> dict:
    """Чем заменить деталь: подходящие — первыми, у неподходящих — причина."""
    return run(advisor.options, q.ids, q.part, q.q, q.limit, q.ram_qty, q.drives, q.drive, q.sort,
               q.filters, q.fit_only)


@app.post("/api/upgrade")
def upgrade(q: UpgradeIn) -> dict:
    """Улучшить сборку за указанную сумму: что заменить, сколько стоит, какой прирост."""
    return run(advisor.upgrade_build, q.ids, q.money, q.task, perf.RES.index(q.res), q.ram_qty, resale=q.resale,
               drives=q.drives)


@app.get("/api/health")
def health() -> dict:
    cat = catalog()
    return {"ok": True, "generated": cat["generated"], "prices": cat.get("prices"),
            "models": {k: len(v) for k, v in cat.items() if isinstance(v, list)}}


@app.middleware("http")
async def revalidate_static(request, call_next):
    """Статика — с проверкой свежести при каждом открытии (ETag → 304): без заголовка браузер
    держал старые app.css и 3D-страницу во фрейме, и правки не было видно даже после Ctrl+F5."""
    resp = await call_next(request)
    if not request.url.path.startswith("/api/"):
        resp.headers["Cache-Control"] = "no-cache"
    return resp


# Каталог (catalog.js, ~1,8 МБ) и история цен — текст, сжимаются в 8–10 раз: на сервере с узким
# каналом 3D без этого открывалась десятки секунд
app.add_middleware(GZipMiddleware, minimum_size=1000)
app.mount("/", StaticFiles(directory=WEB, html=True), name="web")
