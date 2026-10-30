-- Задания сбора: что передаётся адаптеру площадки по каждой категории.
-- У Wildberries это поисковая фраза, у DNS — путь раздела каталога.
-- Хранятся в базе, а не в коде: новая категория или площадка добавляется
-- строкой в таблице, без изменения программы.

CREATE TABLE IF NOT EXISTS collect_targets (
    id              serial PRIMARY KEY,
    marketplace_id  smallint     NOT NULL REFERENCES marketplaces(id),
    category_id     smallint     NOT NULL REFERENCES categories(id),
    query           varchar(255) NOT NULL,
    active          boolean      NOT NULL DEFAULT true,
    UNIQUE (marketplace_id, category_id, query)
);

INSERT INTO collect_targets (marketplace_id, category_id, query)
SELECT m.id, c.id, v.query
FROM (VALUES
    -- Запросы WB намеренно общие: чужие товары отсеивает фильтр по subjectId
    ('wb',  'cpu',    'процессор'),
    ('wb',  'mb',     'материнская плата'),
    ('wb',  'ram',    'оперативная память'),
    ('wb',  'gpu',    'видеокарта'),
    ('wb',  'psu',    'блок питания для пк'),
    ('wb',  'case',   'корпус для пк'),
    ('wb',  'cooler', 'кулер для процессора'),
    ('wb',  'ssd',    'ssd накопитель'),
    ('wb',  'hdd',    'жесткий диск 3.5'),
    -- Разделы каталога DNS, найдены 11.09.2026 через раздел
    -- «Основные комплектующие для ПК». Нужны разделы с товарами, а не
    -- с подразделами: у памяти берётся только DIMM (SO-DIMM — ноутбучная),
    -- у SSD — оба подраздела, SATA и M.2. Кулеры — только воздушные:
    -- жидкостное охлаждение у DNS отдельным разделом
    ('dns', 'cpu',    '/catalog/17a899cd16404e77/processory/'),
    ('dns', 'mb',     '/catalog/17a89a0416404e77/materinskie-platy/'),
    ('dns', 'ram',    '/catalog/17a89a3916404e77/operativnaa-pamat-dimm/'),
    ('dns', 'gpu',    '/catalog/17a89aab16404e77/videokarty/'),
    ('dns', 'psu',    '/catalog/17a89c2216404e77/bloki-pitania/'),
    ('dns', 'case',   '/catalog/17a89c5616404e77/korpusa/'),
    ('dns', 'cooler', '/catalog/17a9cc2d16404e77/kulery-dla-processorov/'),
    ('dns', 'ssd',    '/catalog/8a9ddfba20724e77/ssd-nakopiteli/'),
    ('dns', 'ssd',    '/catalog/dd58148920724e77/ssd-m2-nakopiteli/'),
    -- HDD — только 3,5″: 2,5″ у DNS отдельно и ноутбучные (найдено 12.09.2026)
    ('dns', 'hdd',    '/catalog/17a8914916404e77/zestkie-diski-35/'),
    -- Разделы Ozon: куда сам Ozon уводит поиск по названию категории
    -- (проверено 11.09.2026). Выдача раздела — порциями по 8 товаров
    ('ozon', 'cpu',    '/category/protsessory-15726/'),
    ('ozon', 'mb',     '/category/materinskie-platy-15725/'),
    ('ozon', 'ram',    '/category/operativnaya-pamyat-15724/'),
    ('ozon', 'gpu',    '/category/videokarty-15721/'),
    ('ozon', 'psu',    '/category/bloki-pitaniya-15727/'),
    ('ozon', 'case',   '/category/korpusa-dlya-kompyuterov-15734/'),
    ('ozon', 'cooler', '/category/kulery-dlya-kompyuterov-15730/'),
    ('ozon', 'ssd',    '/category/ssd-nakopiteli-15712/'),
    ('ozon', 'hdd',    '/category/vnutrennie-zhestkie-diski-15713/'),
    -- Запросы AliExpress уточнены: по слову «процессор» Али уводит в
    -- электронные компоненты, по «материнская плата» — в платы для ноутбуков
    -- Одной фразы для процессоров, плат и видеокарт мало: по «core i5» Али
    -- выдаёт в основном мини-ПК и комплекты, в категорию проходит 15 из 100
    ('ali',  'cpu',    'процессор intel core i5'),
    ('ali',  'cpu',    'процессор intel core i7'),
    ('ali',  'cpu',    'процессор amd ryzen'),
    ('ali',  'mb',     'материнская плата lga1700'),
    ('ali',  'mb',     'материнская плата am4'),
    ('ali',  'mb',     'материнская плата am5'),
    ('ali',  'ram',    'оперативная память ddr5'),
    -- По «видеокарта» и «видеокарта nvidia rtx» Али выдаёт в основном
    -- кронштейны и вентиляторы (8 и 0 карт из 80), нужны модели
    ('ali',  'gpu',    'видеокарта amd radeon rx'),
    ('ali',  'gpu',    'видеокарта rtx 3060'),
    ('ali',  'gpu',    'видеокарта rtx 4060'),
    ('ali',  'gpu',    'видеокарта rtx 5060'),
    ('ali',  'gpu',    'видеокарта rx 6600'),
    ('ali',  'psu',    'блок питания atx'),
    ('ali',  'case',   'корпус для пк'),
    ('ali',  'cooler', 'кулер для процессора'),
    ('ali',  'ssd',    'ssd накопитель'),
    ('ali',  'hdd',    'жесткий диск hdd 3.5 sata'),
    -- Поиск Яндекс Маркета точнее всех: по каждой фразе вся выдача
    -- попадает в одну категорию hid (проверено 11.09.2026)
    ('ym',   'cpu',    'процессор'),
    ('ym',   'mb',     'материнская плата'),
    ('ym',   'ram',    'оперативная память'),
    ('ym',   'gpu',    'видеокарта'),
    ('ym',   'psu',    'блок питания для компьютера'),
    ('ym',   'case',   'корпус для компьютера'),
    ('ym',   'cooler', 'кулер для процессора'),
    ('ym',   'ssd',    'ssd накопитель'),
    ('ym',   'hdd',    'жесткий диск 3.5')
) AS v(mp, cat, query)
JOIN marketplaces m ON m.code = v.mp
JOIN categories c   ON c.code = v.cat
ON CONFLICT DO NOTHING;

-- Категории DNS: раздел каталога однозначно задаёт категорию,
-- поэтому внешний идентификатор — код раздела из адреса
INSERT INTO marketplace_categories (marketplace_id, category_id, external_id, external_name)
SELECT m.id, c.id, v.ext, v.label
FROM (VALUES
    ('cpu',    '17a899cd16404e77', 'Процессоры'),
    ('mb',     '17a89a0416404e77', 'Материнские платы'),
    ('ram',    '17a89a3916404e77', 'Оперативная память DIMM'),
    ('gpu',    '17a89aab16404e77', 'Видеокарты'),
    ('psu',    '17a89c2216404e77', 'Блоки питания'),
    ('case',   '17a89c5616404e77', 'Корпуса'),
    ('cooler', '17a9cc2d16404e77', 'Кулеры для процессоров'),
    ('ssd',    '8a9ddfba20724e77', 'SSD накопители'),
    ('ssd',    'dd58148920724e77', 'SSD M.2 накопители'),
    ('hdd',    '17a8914916404e77', 'Жесткие диски 3.5"')
) AS v(cat, ext, label)
JOIN categories c ON c.code = v.cat
JOIN marketplaces m ON m.code = 'dns'
ON CONFLICT DO NOTHING;

-- Категории Ozon: числовой код раздела из адреса
INSERT INTO marketplace_categories (marketplace_id, category_id, external_id, external_name)
SELECT m.id, c.id, v.ext, v.label
FROM (VALUES
    ('cpu',    '15726', 'Процессоры'),
    ('mb',     '15725', 'Материнские платы'),
    ('ram',    '15724', 'Оперативная память'),
    ('gpu',    '15721', 'Видеокарты'),
    ('psu',    '15727', 'Блоки питания'),
    ('case',   '15734', 'Корпуса для компьютеров'),
    ('cooler', '15730', 'Кулеры для компьютеров'),
    ('ssd',    '15712', 'SSD-накопители'),
    ('hdd',    '15713', 'Внутренние жесткие диски')
) AS v(cat, ext, label)
JOIN categories c ON c.code = v.cat
JOIN marketplaces m ON m.code = 'ozon'
ON CONFLICT DO NOTHING;

-- Категории AliExpress (itemData.properties.categoryId). Сопоставлены
-- 11.09.2026 по выдаче: номер считается категорией комплектующих, если
-- под ним лежат настоящие комплектующие. Категория видеокарт засорена
-- держателями и кронштейнами — их отсеивает нормализация, а не сбор.
INSERT INTO marketplace_categories (marketplace_id, category_id, external_id, external_name)
SELECT m.id, c.id, v.ext, v.label
FROM (VALUES
    ('cpu',    '7788', 'Процессоры'),
    ('cpu',    '5759', 'Процессоры (фильтр сайта)'),
    ('mb',     '5762', 'Материнские платы'),
    ('ram',    '5763', 'Модули памяти'),
    ('gpu',    '5761', 'Видеокарты'),
    ('psu',    '5541', 'Блоки питания для компьютеров'),
    ('case',   '5309', 'Компьютерные корпуса'),
    ('cooler', '4958', 'Охладители процессора'),
    ('cooler', '5758', 'Кулеры для процессора'),
    ('ssd',    '1573', 'Твердотельные накопители (SSD)'),
    ('hdd',    '1572', 'Жёсткие диски (HDD)')
) AS v(cat, ext, label)
JOIN categories c ON c.code = v.cat
JOIN marketplaces m ON m.code = 'ali'
ON CONFLICT DO NOTHING;

-- Категории Яндекс Маркета (hid из data-zone-data карточки). Внешние SSD
-- (hid 80110894 и 91032) не включены, как и на других площадках.
INSERT INTO marketplace_categories (marketplace_id, category_id, external_id, external_name)
SELECT m.id, c.id, v.ext, v.label
FROM (VALUES
    ('cpu',    '91019',    'Процессоры'),
    ('mb',     '91020',    'Материнские платы'),
    ('ram',    '191211',   'Оперативная память'),
    ('gpu',    '91031',    'Видеокарты'),
    ('psu',    '857707',   'Блоки питания'),
    ('case',   '91028',    'Корпуса'),
    ('cooler', '818965',   'Кулеры для процессора'),
    ('ssd',    '16309373', 'Внутренние SSD'),
    ('hdd',    '91033',    'Внутренние жесткие диски')
) AS v(cat, ext, label)
JOIN categories c ON c.code = v.cat
JOIN marketplaces m ON m.code = 'ym'
ON CONFLICT DO NOTHING;
