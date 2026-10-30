-- Представления для просмотра базы человеком. Колонки названы по-русски:
-- они для глаз, программа читает исходные таблицы.

-- Предложения с последней ценой
CREATE OR REPLACE VIEW v_offers AS
SELECT o.id,
       c.name                AS категория,
       o.source_name         AS наименование,
       o.brand               AS бренд,
       p.price               AS цена,
       p.price_basic         AS цена_до_скидки,
       p.quantity            AS остаток,
       o.seller              AS продавец,
       m.name                AS площадка,
       o.rating              AS рейтинг,
       o.feedbacks           AS отзывов,
       o.url                 AS ссылка,
       p.collected_at AT TIME ZONE 'Europe/Moscow' AS цена_на
FROM offers o
JOIN categories c   ON c.id = o.category_id
JOIN marketplaces m ON m.id = o.marketplace_id
LEFT JOIN LATERAL (
    SELECT price, price_basic, quantity, collected_at
    FROM price_snapshots ps
    WHERE ps.offer_id = o.id
    ORDER BY collected_at DESC
    LIMIT 1
) p ON true;

-- Журнал сеансов сбора
CREATE OR REPLACE VIEW v_sessions AS
SELECT s.id,
       m.name                AS площадка,
       c.name                AS категория,
       s.query               AS запрос,
       s.status              AS статус,
       s.pages               AS страниц,
       s.received            AS позиций,
       s.new_offers          AS новых,
       s.skipped             AS отсеяно,
       s.started_at AT TIME ZONE 'Europe/Moscow' AS начало,
       date_trunc('second', s.finished_at - s.started_at) AS длительность,
       s.error               AS ошибка
FROM collect_sessions s
JOIN marketplaces m    ON m.id = s.marketplace_id
LEFT JOIN categories c ON c.id = s.category_id;

-- Сводка по категориям: сколько собрано и в каком диапазоне цены
CREATE OR REPLACE VIEW v_categories AS
SELECT категория,
       count(*)                                                      AS предложений,
       count(DISTINCT продавец)                                      AS продавцов,
       min(цена)                                                     AS мин_цена,
       round(percentile_cont(0.5) WITHIN GROUP (ORDER BY цена)::numeric) AS медиана,
       max(цена)                                                     AS макс_цена
FROM v_offers
GROUP BY категория;
