-- Міграція 2: Виправлення відображення NULL-категорій у VIEW
-- Запусти цей файл ОДНИМ БЛОКОМ у Supabase SQL Editor

-- Оновлюємо VIEW top_expense_categories:
-- Використовуємо COALESCE щоб NULL категорії відображались як "Інше"
DROP VIEW IF EXISTS top_expense_categories;
CREATE OR REPLACE VIEW top_expense_categories AS
SELECT
    t.user_id,
    COALESCE(c.name, 'Інше')   AS category_name,
    COALESCE(c.icon, '💸')     AS icon,
    SUM(t.amount)              AS total,
    COUNT(*)                   AS tx_count
FROM transactions t
LEFT JOIN categories c ON t.category_id = c.id
WHERE
    t.type = 'expense'
    AND t.ignore_in_stats = FALSE
    AND t.transaction_date >= DATE_TRUNC('month', NOW())
GROUP BY t.user_id, COALESCE(c.name, 'Інше'), COALESCE(c.icon, '💸')
ORDER BY total DESC;
