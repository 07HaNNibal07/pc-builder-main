"""Единый интерфейс адаптера торговой площадки.

Каждая площадка подключается отдельным адаптером. Остальная система не
знает, как устроен источник: от адаптера она получает предложения в
едином виде. Добавить площадку — значит написать один класс.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from decimal import Decimal


class SourceError(Exception):
    """Источник не отдал данные: блокировка, лимит частоты, сбой сети."""


@dataclass
class OfferData:
    """Предложение площадки в едином виде."""
    external_id: str
    # Категория в терминах площадки (subjectId у WB): по ней отсеиваются
    # чужие товары, которые поиск подмешивает в выдачу
    category_ref: str
    source_name: str
    url: str
    price: Decimal
    price_basic: Decimal | None = None
    quantity: int | None = None
    brand: str | None = None
    seller: str | None = None
    rating: float | None = None
    feedbacks: int | None = None
    # Признак наличия для площадок, которые не раскрывают количество (DNS)
    available: bool | None = None
    raw: dict = field(default_factory=dict)

    @property
    def in_stock(self) -> bool:
        if self.available is not None:
            return self.available
        return self.quantity is None or self.quantity > 0


@dataclass
class Page:
    """Страница поисковой выдачи."""
    offers: list[OfferData]
    # Позиции без цены: товар есть в выдаче, но купить его нельзя
    unpriced: int = 0
    last: bool = False


class Adapter(ABC):
    code: str        # код площадки в таблице marketplaces
    page_size: int

    @abstractmethod
    def search(self, query: str, page: int) -> Page:
        """Страница выдачи по запросу; при отказе источника — SourceError.

        Смысл запроса определяет площадка: поисковая фраза у WB,
        путь раздела каталога у DNS. Задания хранятся в collect_targets.
        """

    def accepts(self, offer: OfferData, category: str, allowed: set[str]) -> bool:
        """Относится ли предложение к собираемой категории.

        По умолчанию — по категории площадки из marketplace_categories.
        Площадка, у которой номер категории ненадёжен, переопределяет проверку.
        """
        return offer.category_ref in allowed

    def close(self) -> None:
        """Освободить ресурсы: браузер, сетевые соединения."""
