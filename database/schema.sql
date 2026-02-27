-- ============================================================
--  FinanceOS Bot — Supabase / PostgreSQL Schema Migration
--  Виконати в: Supabase Dashboard → SQL Editor → New query
-- ============================================================

-- 1. Вмикаємо розширення pgvector (потрібно виконати ОДИН РАЗ)
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";
CREATE EXTENSION IF NOT EXISTS vector;

-- ============================================================
-- 2. ENUM типи
-- ============================================================

CREATE TYPE transaction_type   AS ENUM ('income', 'expense', 'transfer');
CREATE TYPE transaction_source AS ENUM ('manual', 'csv');
CREATE TYPE category_type      AS ENUM ('income', 'expense', 'transfer');
CREATE TYPE goal_status        AS ENUM ('active', 'completed', 'failed');
CREATE TYPE message_role       AS ENUM ('user', 'ai', 'system');

-- ============================================================
-- 3. Таблиця USERS
--    Один рядок = один Telegram-користувач
-- ============================================================

CREATE TABLE IF NOT EXISTS users (
    id              UUID          PRIMARY KEY DEFAULT uuid_generate_v4(),
    tg_id           BIGINT        NOT NULL UNIQUE,          -- Telegram user ID
    tg_username     VARCHAR(64),                            -- @username (може бути NULL)
    full_name       VARCHAR(128),                           -- Ім'я з профілю Telegram
    currency        VARCHAR(8)    NOT NULL DEFAULT 'UAH',   -- Валюта за замовчуванням
    monthly_income  NUMERIC(12,2) DEFAULT 0,                -- Місячний дохід (встановлюється при онбордингу)
    comfort_level   SMALLINT      NOT NULL DEFAULT 5        -- 1–10: наскільки агресивно рекомендувати заощадження
                    CHECK (comfort_level BETWEEN 1 AND 10),
    communication_style VARCHAR(16) NOT NULL DEFAULT 'balanced',  -- casual / balanced / formal
    onboarded       BOOLEAN       NOT NULL DEFAULT FALSE,   -- Пройшов онбординг?
    created_at      TIMESTAMPTZ   NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMPTZ   NOT NULL DEFAULT NOW()
);

-- Автоматично оновлюємо updated_at при кожному UPDATE
CREATE OR REPLACE FUNCTION update_updated_at_column()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = NOW();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER trg_users_updated_at
    BEFORE UPDATE ON users
    FOR EACH ROW EXECUTE FUNCTION update_updated_at_column();

-- ============================================================
-- 4. Таблиця CATEGORIES
--    Стандартні категорії (user_id IS NULL) + кастомні юзера
-- ============================================================

CREATE TABLE IF NOT EXISTS categories (
    id          UUID           PRIMARY KEY DEFAULT uuid_generate_v4(),
    user_id     UUID           REFERENCES users(id) ON DELETE CASCADE,  -- NULL = глобальна
    name        VARCHAR(64)    NOT NULL,
    type        category_type  NOT NULL,
    icon        VARCHAR(8)     DEFAULT '📂',                             -- Emoji іконка
    is_default  BOOLEAN        NOT NULL DEFAULT FALSE,                  -- Вбудована чи кастомна
    created_at  TIMESTAMPTZ    NOT NULL DEFAULT NOW(),

    UNIQUE (user_id, name)      -- Один юзер не може мати дві однакові категорії
);

-- Індекс для швидкого отримання категорій конкретного юзера + глобальних
CREATE INDEX idx_categories_user_id ON categories(user_id);

-- Стандартні (глобальні) категорії — будуть доступні ВСІМ користувачам
INSERT INTO categories (user_id, name, type, icon, is_default) VALUES
    -- 🍎 Харчування (expense)
    (NULL, 'Супермаркети',  'expense', '🛒', TRUE),
    (NULL, 'Заклади',       'expense', '🍽️', TRUE),
    (NULL, 'Кава/Снеки',    'expense', '☕', TRUE),

    -- 🏠 Житло та Побут (expense)
    (NULL, 'Оренда/Комунальні', 'expense', '🏠', TRUE),
    (NULL, 'Товари для дому',   'expense', '🛋️', TRUE),
    (NULL, 'Зв''язок',          'expense', '📱', TRUE),

    -- 🚗 Транспорт (expense)
    (NULL, 'Таксі/Громадський', 'expense', '🚕', TRUE),
    (NULL, 'Авто',              'expense', '🚗', TRUE),

    -- 💻 Робота та Навчання (expense)
    (NULL, 'Сервіси/Підписки', 'expense', '💻', TRUE),
    (NULL, 'Освіта',           'expense', '📚', TRUE),
    (NULL, 'Техніка',          'expense', '⌨️', TRUE),

    -- 💊 Здоров'я та Спорт (expense)
    (NULL, 'Ліки/Лікарі', 'expense', '💊', TRUE),
    (NULL, 'Спортзал',    'expense', '🏋️', TRUE),
    (NULL, 'Б''юті',       'expense', '💈', TRUE),

    -- 👕 Шопінг (expense)
    (NULL, 'Одяг/Взуття', 'expense', '👗', TRUE),
    (NULL, 'Електроніка', 'expense', '🎮', TRUE),

    -- 🎉 Розваги (expense)
    (NULL, 'Розважальні підписки', 'expense', '🍿', TRUE),
    (NULL, 'Події/Хобі',           'expense', '🎟️', TRUE),

    -- 🤝 Донати та Благодійність (expense)
    (NULL, 'ЗСУ/Волонтери', 'expense', '🇺🇦', TRUE),
    (NULL, 'Допомога рідним', 'expense', '🫶', TRUE),

    -- 🔄 Фінансові операції (transfer / expense)
    (NULL, 'Комісії банків',   'expense',  '💸', TRUE),
    (NULL, 'Інвестиції/Скарбничка', 'transfer', '📈', TRUE),
    (NULL, 'Обмін валют',      'transfer', '💱', TRUE),
    (NULL, 'Переказ (інше)',   'transfer', '🔄', TRUE),

    -- 🟢 Доходи (income)
    (NULL, 'Зарплата',    'income', '💰', TRUE),
    (NULL, 'Фріланс',     'income', '👨‍💻', TRUE),
    (NULL, 'Подарунок',   'income', '🎁', TRUE),
    (NULL, 'Інший дохід', 'income', '💵', TRUE);

-- ============================================================
-- 5. Таблиця TRANSACTIONS
--    Кожна фінансова операція юзера
-- ============================================================

CREATE TABLE IF NOT EXISTS transactions (
    id               UUID                PRIMARY KEY DEFAULT uuid_generate_v4(),
    user_id          UUID                NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    category_id      UUID                REFERENCES categories(id) ON DELETE SET NULL,
    amount           NUMERIC(12,2)       NOT NULL CHECK (amount > 0),  -- Завжди позитивне
    type             transaction_type    NOT NULL,                      -- 'income', 'expense' або 'transfer'
    description      TEXT,                                              -- Текстовий опис (з повідомлення або CSV)
    source           transaction_source  NOT NULL DEFAULT 'manual',    -- Звідки прийшла транзакція
    transaction_date TIMESTAMPTZ         NOT NULL DEFAULT NOW(),        -- Дата операції
    raw_text         TEXT,                                              -- Оригінальне повідомлення від юзера (для дебагу)
    metadata         JSONB               DEFAULT '{}',                  -- Для CSV: додаткові поля виписки
    ignore_in_stats  BOOLEAN             NOT NULL DEFAULT FALSE,        -- TRUE якщо операцію треба виключити зі статистики (борги, спільні чеки)
    created_at       TIMESTAMPTZ         NOT NULL DEFAULT NOW()
);

-- Основний індекс для звітів: транзакції юзера за date range
CREATE INDEX idx_transactions_user_date
    ON transactions(user_id, transaction_date DESC);

-- Індекс для фільтрації по типу (дохід/витрата) в межах юзера
CREATE INDEX idx_transactions_user_type
    ON transactions(user_id, type);

-- GIN індекс для JSONB пошуку по metadata (CSV додаткові поля)
CREATE INDEX idx_transactions_metadata
    ON transactions USING GIN(metadata);

-- ============================================================
-- 6. Таблиця GOALS
--    Фінансові цілі накопичення
-- ============================================================

CREATE TABLE IF NOT EXISTS goals (
    id               UUID          PRIMARY KEY DEFAULT uuid_generate_v4(),
    user_id          UUID          NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    name             VARCHAR(128)  NOT NULL,              -- "Планшет", "Відпустка"
    target_amount    NUMERIC(12,2) NOT NULL CHECK (target_amount > 0),
    current_amount   NUMERIC(12,2) NOT NULL DEFAULT 0,   -- Поточний прогрес (оновлюється вручну або автоматично)
    monthly_deposit  NUMERIC(12,2),                      -- Рекомендований щомісячний внесок (розраховується AI)
    deadline         DATE,                               -- Бажана дата досягнення
    status           goal_status   NOT NULL DEFAULT 'active',
    created_at       TIMESTAMPTZ   NOT NULL DEFAULT NOW(),
    updated_at       TIMESTAMPTZ   NOT NULL DEFAULT NOW()
);

CREATE TRIGGER trg_goals_updated_at
    BEFORE UPDATE ON goals
    FOR EACH ROW EXECUTE FUNCTION update_updated_at_column();

-- Індекс для завантаження активних цілей юзера
CREATE INDEX idx_goals_user_status ON goals(user_id, status);

-- ============================================================
-- 7. Таблиця CONVERSATION_MEMORY
--    Стиснута пам'ять AI-розмов (між сесіями)
-- ============================================================

CREATE TABLE IF NOT EXISTS conversation_memory (
    id           UUID          PRIMARY KEY DEFAULT uuid_generate_v4(),
    user_id      UUID          NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    role         message_role  NOT NULL,       -- 'user', 'ai', або 'system' (для summary)
    content      TEXT          NOT NULL,
    token_count  INT           NOT NULL DEFAULT 0,  -- Приблизна кількість токенів
    is_summary   BOOLEAN       NOT NULL DEFAULT FALSE,  -- TRUE якщо це стиснений summary
    created_at   TIMESTAMPTZ   NOT NULL DEFAULT NOW()
);

-- Індекс для завантаження останніх повідомлень юзера
CREATE INDEX idx_memory_user_time
    ON conversation_memory(user_id, created_at DESC);

-- ============================================================
-- 8. Таблиця EMBEDDINGS (pgvector)
--    Векторні представлення транзакцій для семантичного пошуку
-- ============================================================

CREATE TABLE IF NOT EXISTS embeddings (
    id             UUID         PRIMARY KEY DEFAULT uuid_generate_v4(),
    user_id        UUID         NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    transaction_id UUID         REFERENCES transactions(id) ON DELETE CASCADE,
    content        TEXT         NOT NULL,        -- Текст з якого зроблено ембединг
    embedding      vector(384)  NOT NULL,        -- all-MiniLM-L6-v2 видає 384-мірні вектори
    metadata       JSONB        DEFAULT '{}',    -- Категорія, дата, тип транзакції
    created_at     TIMESTAMPTZ  NOT NULL DEFAULT NOW()
);

-- HNSW індекс — ідеальний для поступового додавання рядків (не потребує batch training)
-- m=16: баланс між якістю пошуку та споживанням RAM
-- ef_construction=64: якість побудови графу (більше = повільніше, але точніше)
CREATE INDEX idx_embeddings_hnsw
    ON embeddings USING hnsw (embedding vector_cosine_ops)
    WITH (m = 16, ef_construction = 64);

-- Індекс для ізоляції пошуку по юзеру
CREATE INDEX idx_embeddings_user_id ON embeddings(user_id);

-- ============================================================
-- 9. RPC Функція для векторного пошуку (Supabase Vector Search)
--    Викликається через supabase.rpc('match_embeddings', {...})
-- ============================================================

CREATE OR REPLACE FUNCTION match_embeddings(
    query_embedding  vector(384),
    p_user_id        UUID,
    match_count      INT DEFAULT 5,
    match_threshold  FLOAT DEFAULT 0.7
)
RETURNS TABLE (
    id             UUID,
    transaction_id UUID,
    content        TEXT,
    metadata       JSONB,
    similarity     FLOAT
)
LANGUAGE plpgsql
AS $$
BEGIN
    RETURN QUERY
    SELECT
        e.id,
        e.transaction_id,
        e.content,
        e.metadata,
        1 - (e.embedding <=> query_embedding) AS similarity  -- cosine similarity
    FROM embeddings e
    WHERE
        e.user_id = p_user_id
        AND 1 - (e.embedding <=> query_embedding) > match_threshold
    ORDER BY e.embedding <=> query_embedding  -- <=> = cosine distance (менше = краще)
    LIMIT match_count;
END;
$$;

-- ============================================================
-- 9b. RPC Функція для отримання трендів витрат по місяцях
-- ============================================================
CREATE OR REPLACE FUNCTION get_spending_trends(
    p_user_id UUID,
    p_months INT DEFAULT 3
)
RETURNS TABLE (
    month_period TEXT,
    total_income NUMERIC,
    total_expenses NUMERIC
)
LANGUAGE plpgsql
AS $$
BEGIN
    RETURN QUERY
    SELECT
        TO_CHAR(DATE_TRUNC('month', transaction_date), 'YYYY-MM') AS month_period,
        SUM(CASE WHEN type = 'income' THEN amount ELSE 0 END) AS total_income,
        SUM(CASE WHEN type = 'expense' THEN amount ELSE 0 END) AS total_expenses
    FROM transactions
    WHERE user_id = p_user_id
      AND type != 'transfer'
      AND ignore_in_stats = FALSE
      AND transaction_date >= DATE_TRUNC('month', CURRENT_DATE - (p_months || ' months')::INTERVAL)
    GROUP BY DATE_TRUNC('month', transaction_date)
    ORDER BY DATE_TRUNC('month', transaction_date) DESC;
END;
$$;

-- ============================================================
-- 10. Row Level Security (RLS)
--     Для цього бота ми використовуємо Service Role Key (бекенд),
--     тому RLS активуємо але додаємо bypass policy для service role.
--     Це захищає дані якщо хтось отримає anon key.
-- ============================================================

ALTER TABLE users               ENABLE ROW LEVEL SECURITY;
ALTER TABLE categories          ENABLE ROW LEVEL SECURITY;
ALTER TABLE transactions        ENABLE ROW LEVEL SECURITY;
ALTER TABLE goals               ENABLE ROW LEVEL SECURITY;
ALTER TABLE conversation_memory ENABLE ROW LEVEL SECURITY;
ALTER TABLE embeddings          ENABLE ROW LEVEL SECURITY;

-- Service role bypasses RLS автоматично в Supabase.
-- Але для anon/authenticated ролей — забороняємо все.
-- Це запобігає несанкціонованому доступу через anon key.

CREATE POLICY "deny_anon_users"        ON users               FOR ALL TO anon USING (FALSE);
CREATE POLICY "deny_anon_categories"   ON categories          FOR ALL TO anon USING (FALSE);
CREATE POLICY "deny_anon_transactions" ON transactions         FOR ALL TO anon USING (FALSE);
CREATE POLICY "deny_anon_goals"        ON goals               FOR ALL TO anon USING (FALSE);
CREATE POLICY "deny_anon_memory"       ON conversation_memory FOR ALL TO anon USING (FALSE);
CREATE POLICY "deny_anon_embeddings"   ON embeddings          FOR ALL TO anon USING (FALSE);

-- ============================================================
-- 11. Корисні VIEW для агрегованих звітів
-- ============================================================

-- Поточний баланс юзера за поточний місяць
CREATE OR REPLACE VIEW monthly_balance AS
SELECT
    user_id,
    SUM(CASE WHEN type = 'income'  THEN amount ELSE 0 END) AS total_income,
    SUM(CASE WHEN type = 'expense' THEN amount ELSE 0 END) AS total_expenses,
    SUM(CASE WHEN type = 'income'  THEN amount ELSE -amount END) AS net_balance,
    DATE_TRUNC('month', NOW()) AS period
FROM transactions
WHERE transaction_date >= DATE_TRUNC('month', NOW())
  AND type != 'transfer'
  AND ignore_in_stats = FALSE
GROUP BY user_id;

-- Топ-5 категорій витрат юзера за поточний місяць
CREATE OR REPLACE VIEW top_expense_categories AS
SELECT
    t.user_id,
    c.name AS category_name,
    c.icon,
    SUM(t.amount) AS total,
    COUNT(*) AS tx_count
FROM transactions t
LEFT JOIN categories c ON t.category_id = c.id
WHERE
    t.type = 'expense'
    AND t.ignore_in_stats = FALSE
    AND t.transaction_date >= DATE_TRUNC('month', NOW())
GROUP BY t.user_id, c.name, c.icon
ORDER BY total DESC;

-- ============================================================
-- 12. Таблиця FSM_STATES
--     Для зберігання стану aiogram бота при рестартах інстансу
-- ============================================================

CREATE TABLE IF NOT EXISTS fsm_states (
    storage_key VARCHAR PRIMARY KEY,     -- bot_id:chat_id:user_id:thread_id:destiny
    state       VARCHAR,                 -- Поточний стан (наприклад 'AddTransactionStates:waiting_for_confirm')
    data        JSONB DEFAULT '{}',      -- Додаткові дані FSM
    updated_at  TIMESTAMPTZ DEFAULT NOW()
);

CREATE TRIGGER trg_fsm_states_updated_at
    BEFORE UPDATE ON fsm_states
    FOR EACH ROW EXECUTE FUNCTION update_updated_at_column();

ALTER TABLE fsm_states ENABLE ROW LEVEL SECURITY;
CREATE POLICY "deny_anon_fsm" ON fsm_states FOR ALL TO anon USING (FALSE);
