-- Модель данных системы подбора конфигурации ПК.
--
-- Разделены три сущности: модель (изделие производителя), предложение
-- (модель на конкретной площадке) и ценовой срез (цена предложения в
-- момент сбора). Разделение даёт историю цен без дублирования описаний
-- и позволяет одной модели соответствовать множеству предложений.

-- Справочники -------------------------------------------------------------

CREATE TABLE marketplaces (
    id          smallserial PRIMARY KEY,
    code        varchar(16)  NOT NULL UNIQUE,   -- wb, ozon, dns, ym, ali
    name        varchar(64)  NOT NULL,
    -- api — программный интерфейс, browser — сбор через управляемый браузер
    kind        varchar(16)  NOT NULL CHECK (kind IN ('api', 'browser')),
    base_url    varchar(255) NOT NULL
);

CREATE TABLE categories (
    id          smallserial PRIMARY KEY,
    code        varchar(16)  NOT NULL UNIQUE,   -- cpu, mb, ram, gpu, psu, case, cooler, ssd
    name        varchar(64)  NOT NULL
);

-- Категория площадки: как категория комплектующих называется в источнике.
-- У WB это subjectId; одна наша категория может соответствовать нескольким.
CREATE TABLE marketplace_categories (
    marketplace_id  smallint     NOT NULL REFERENCES marketplaces(id),
    category_id     smallint     NOT NULL REFERENCES categories(id),
    external_id     varchar(64)  NOT NULL,
    external_name   varchar(255),
    PRIMARY KEY (marketplace_id, external_id)
);

-- Модель ------------------------------------------------------------------

-- Изделие производителя. Тождество модели задаёт артикул производителя:
-- физически одна видеокарта на пяти площадках — одна запись здесь.
CREATE TABLE models (
    id          serial PRIMARY KEY,
    category_id smallint     NOT NULL REFERENCES categories(id),
    brand       varchar(64)  NOT NULL,
    article     varchar(128),                    -- артикул производителя
    name        varchar(512) NOT NULL,           -- каноническое наименование
    -- Нормализованные характеристики: сокет, тип памяти, TDP, длина и т. д.
    -- Хранятся в JSONB, потому что набор полей у категорий разный.
    specs       jsonb        NOT NULL DEFAULT '{}',
    created_at  timestamptz  NOT NULL DEFAULT now()
);
-- Артикул уникален в пределах бренда; у моделей без артикула ограничения нет.
CREATE UNIQUE INDEX models_brand_article ON models (lower(brand), upper(article))
    WHERE article IS NOT NULL;
CREATE INDEX models_category ON models (category_id);

-- Предложение -------------------------------------------------------------

-- Позиция на площадке. Появляется при первом сборе и живёт, пока площадка
-- её отдаёт; привязка к модели появляется после сопоставления.
CREATE TABLE offers (
    id              serial PRIMARY KEY,
    marketplace_id  smallint     NOT NULL REFERENCES marketplaces(id),
    category_id     smallint     NOT NULL REFERENCES categories(id),
    external_id     varchar(64)  NOT NULL,       -- nm у WB, SKU у Ozon
    model_id        integer      REFERENCES models(id) ON DELETE SET NULL,
    source_name     varchar(512) NOT NULL,       -- наименование в источнике, как есть
    brand           varchar(128),
    seller          varchar(255),
    url             varchar(512) NOT NULL,
    rating          numeric(3,2),
    feedbacks       integer,
    -- Последний полученный ответ источника целиком: позволяет пересчитать
    -- нормализацию новым алгоритмом, не обращаясь к площадке повторно.
    raw             jsonb,
    first_seen_at   timestamptz  NOT NULL DEFAULT now(),
    last_seen_at    timestamptz  NOT NULL DEFAULT now(),
    UNIQUE (marketplace_id, external_id)
);
CREATE INDEX offers_model ON offers (model_id);
CREATE INDEX offers_category ON offers (category_id);

-- Характеристики со страницы товара как есть: {название: значение}. Хранятся
-- отдельно от models.specs: те пересчитывает нормализация, а это — сырьё,
-- которое сводится к модели при построении каталога (collector.catalog).
-- Пустой props — карточка не найдена; по fetched_at её запрашивают снова.
CREATE TABLE offer_props (
    offer_id        integer      PRIMARY KEY REFERENCES offers(id) ON DELETE CASCADE,
    props           jsonb        NOT NULL,
    fetched_at      timestamptz  NOT NULL DEFAULT now()
);

-- Сеанс сбора -------------------------------------------------------------

CREATE TABLE collect_sessions (
    id              serial PRIMARY KEY,
    marketplace_id  smallint     NOT NULL REFERENCES marketplaces(id),
    category_id     smallint     REFERENCES categories(id),
    query           varchar(255),
    started_at      timestamptz  NOT NULL DEFAULT now(),
    finished_at     timestamptz,
    status          varchar(16)  NOT NULL DEFAULT 'running'
                    CHECK (status IN ('running', 'ok', 'partial', 'failed')),
    pages           integer      NOT NULL DEFAULT 0,
    received        integer      NOT NULL DEFAULT 0,   -- получено позиций
    new_offers      integer      NOT NULL DEFAULT 0,   -- из них впервые
    skipped         integer      NOT NULL DEFAULT 0,   -- отсеяно: чужая категория или нет цены
    error           text
);

-- Ценовой срез ------------------------------------------------------------

CREATE TABLE price_snapshots (
    id              bigserial PRIMARY KEY,
    offer_id        integer      NOT NULL REFERENCES offers(id) ON DELETE CASCADE,
    session_id      integer      REFERENCES collect_sessions(id) ON DELETE SET NULL,
    price           numeric(12,2) NOT NULL,          -- цена для покупателя
    price_basic     numeric(12,2),                   -- цена до скидки
    in_stock        boolean      NOT NULL,
    quantity        integer,
    collected_at    timestamptz  NOT NULL DEFAULT now()
);
CREATE INDEX price_snapshots_offer_time ON price_snapshots (offer_id, collected_at DESC);

-- Сопоставление -----------------------------------------------------------

-- Решение о тождестве предложения и модели вместе с его обоснованием.
-- Решения оператора пополняют эталонную выборку для оценки алгоритма.
CREATE TABLE matches (
    id          serial PRIMARY KEY,
    offer_id    integer      NOT NULL UNIQUE REFERENCES offers(id) ON DELETE CASCADE,
    model_id    integer      REFERENCES models(id) ON DELETE CASCADE,
    similarity  numeric(5,4),
    -- article — по артикулу, name — по модельному имени эталона (DNS),
    -- fuzzy — по признакам названия, manual — решение оператора
    method      varchar(16)  NOT NULL CHECK (method IN ('article', 'name', 'fuzzy', 'manual')),
    confirmed   boolean      NOT NULL DEFAULT false,
    created_at  timestamptz  NOT NULL DEFAULT now()
);

-- Правило совместимости ---------------------------------------------------

-- Условие над характеристиками двух категорий. Правила хранятся в базе,
-- а не в коде: новое правило добавляется без изменения программы.
CREATE TABLE compatibility_rules (
    id          smallserial PRIMARY KEY,
    code        varchar(32)  NOT NULL UNIQUE,
    category_a  smallint     NOT NULL REFERENCES categories(id),
    attr_a      varchar(64)  NOT NULL,
    category_b  smallint     NOT NULL REFERENCES categories(id),
    attr_b      varchar(64)  NOT NULL,
    -- eq — равенство, in — значение A входит в список B,
    -- lte — A не больше B, sum_lte — сумма по сборке с коэффициентом не больше B
    condition   varchar(16)  NOT NULL CHECK (condition IN ('eq', 'in', 'lte', 'gte', 'sum_lte')),
    params      jsonb        NOT NULL DEFAULT '{}',
    -- Шаблон объяснения с конкретными числами, а не просто «несовместимо»
    message     varchar(255) NOT NULL
);

-- Начальное наполнение ----------------------------------------------------

INSERT INTO marketplaces (code, name, kind, base_url) VALUES
    ('wb',   'Wildberries',    'api',     'https://www.wildberries.ru'),
    ('ozon', 'Ozon',           'browser', 'https://www.ozon.ru'),
    ('dns',  'DNS',            'browser', 'https://www.dns-shop.ru'),
    ('ym',   'Яндекс Маркет',  'browser', 'https://market.yandex.ru'),
    ('ali',  'AliExpress',     'browser', 'https://aliexpress.ru');

INSERT INTO categories (code, name) VALUES
    ('cpu',    'Процессор'),
    ('mb',     'Материнская плата'),
    ('ram',    'Оперативная память'),
    ('gpu',    'Видеокарта'),
    ('psu',    'Блок питания'),
    ('case',   'Корпус'),
    ('cooler', 'Кулер процессора'),
    ('ssd',    'Накопитель SSD'),
    ('hdd',    'Жёсткий диск HDD');

-- Категории Wildberries (subjectId). Определены 10.09.2026 по выдаче на
-- запросы каждой категории; внешние SSD (5024) намеренно не включены.
INSERT INTO marketplace_categories (marketplace_id, category_id, external_id, external_name)
SELECT m.id, c.id, v.ext, v.label
FROM (VALUES
    ('cpu',    '3698', 'Процессоры'),
    ('mb',     '3690', 'Материнские платы'),
    ('ram',    '3357', 'Модули памяти'),
    ('gpu',    '3274', 'Видеокарты'),
    ('psu',    '8994', 'Блоки питания для ПК'),
    ('psu',    '2009', 'Блоки питания ATX'),
    ('case',   '4066', 'Корпуса'),
    ('cooler', '5174', 'Кулеры для процессора'),
    ('cooler', '5172', 'Кулеры для процессора'),
    ('ssd',    '5320', 'Внутренние SSD'),
    ('hdd',    '525',  'Жесткие диски')
) AS v(cat, ext, label)
JOIN categories c ON c.code = v.cat
JOIN marketplaces m ON m.code = 'wb';

-- Девять правил из описания работы (раздел 6.2)
INSERT INTO compatibility_rules (code, category_a, attr_a, category_b, attr_b, condition, params, message)
SELECT r.code, a.id, r.attr_a, b.id, r.attr_b, r.condition, r.params::jsonb, r.message
FROM (VALUES
    ('socket',       'cpu',    'socket',       'mb',   'socket',          'eq',      '{}',
     'Сокет процессора {a} не совпадает с сокетом платы {b}'),
    ('ram_type',     'ram',    'memory_type',  'mb',   'memory_type',     'eq',      '{}',
     'Память {a} не поддерживается платой с разъёмами {b}'),
    ('ram_speed',    'ram',    'speed_mhz',    'mb',   'max_memory_mhz',  'lte',     '{}',
     'Частота памяти {a} МГц выше максимальной для платы {b} МГц'),
    ('ram_slots',    'ram',    'modules',      'mb',   'memory_slots',    'lte',     '{}',
     'Модулей памяти {a}, а слотов на плате {b}'),
    ('psu_power',    'psu',    'power_w',      'gpu',  'tdp_w',           'sum_lte', '{"reserve": 1.3, "sum_of": ["cpu.tdp_w", "gpu.tdp_w"]}',
     'Потребление сборки с запасом {a} Вт превышает мощность блока питания {b} Вт'),
    ('gpu_length',   'gpu',    'length_mm',    'case', 'max_gpu_length_mm', 'lte',   '{}',
     'Видеокарта длиной {a} мм не помещается в корпус с ограничением {b} мм'),
    ('cooler_height','cooler', 'height_mm',    'case', 'max_cooler_height_mm', 'lte', '{}',
     'Кулер высотой {a} мм не помещается в корпус с ограничением {b} мм'),
    ('form_factor',  'mb',     'form_factor',  'case', 'form_factors',    'in',      '{}',
     'Корпус не поддерживает плату формата {a}'),
    ('gpu_power_connectors', 'gpu', 'power_connectors', 'psu', 'gpu_connectors', 'in', '{}',
     'На блоке питания нет разъёма {a}, нужного видеокарте')
) AS r(code, cat_a, attr_a, cat_b, attr_b, condition, params, message)
JOIN categories a ON a.code = r.cat_a
JOIN categories b ON b.code = r.cat_b;
