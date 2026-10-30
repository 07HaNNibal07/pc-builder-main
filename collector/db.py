"""Подключение к базе данных.

Сборщик запускается на стороне Windows: там российский IP и нет VPN WSL,
который закрывает доступ к площадкам. База — в Docker, порт опубликован
на локальный адрес Windows.
"""
from __future__ import annotations

import os

import psycopg

DSN = os.environ.get(
    "PCB_DSN", "host=127.0.0.1 port=5433 user=pcb password=pcb dbname=pcb"
)


def connect() -> psycopg.Connection:
    return psycopg.connect(DSN)
