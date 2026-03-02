-- ============================================================
-- Migration 03 — Behavior Analytics Fields
-- Виконати в: Supabase Dashboard → SQL Editor → New query
-- ============================================================

-- Нові поля для автоматичної аналітики фінансової поведінки
ALTER TABLE users ADD COLUMN IF NOT EXISTS monthly_income_actual   NUMERIC(12,2);   -- Реальний середній дохід (авто)
ALTER TABLE users ADD COLUMN IF NOT EXISTS monthly_expenses_actual NUMERIC(12,2);   -- Реальні середні витрати (авто)
ALTER TABLE users ADD COLUMN IF NOT EXISTS spending_style          VARCHAR(16);      -- 'economical' | 'balanced' | 'spender'
ALTER TABLE users ADD COLUMN IF NOT EXISTS behavior_updated_at     TIMESTAMPTZ;     -- Коли останній раз перераховано

-- Також перейменовуємо семантично: monthly_income тепер = очікуваний дохід (довідкова цифра)
-- Фізичне ім'я колонки НЕ змінюємо щоб не ламати код — зміна тільки в логіці застосунку.
-- У коментарі нагадуємо:
COMMENT ON COLUMN users.monthly_income IS 'Очікуваний/плановий дохід (вказується при онбордингу). НЕ впливає на баланс автоматично.';
COMMENT ON COLUMN users.monthly_income_actual IS 'Середній реальний дохід за останні 3 місяці (розраховується автоматично).';
COMMENT ON COLUMN users.monthly_expenses_actual IS 'Середні реальні витрати за останні 3 місяці (розраховується автоматично).';
COMMENT ON COLUMN users.spending_style IS 'Тип фінансової поведінки: economical (<60% доходу), balanced (60-85%), spender (>85%).';
