# FinanceOS Bot

Telegram-бот для особистих фінансів з AI-аналітикою, побудований на базі **aiogram 3** + **Supabase** + **Groq (LLaMA 3)**.

---

## Що вміє

- **Облік витрат і доходів** — просто пишеш у чат "витратив 350 на каву" — бот розпізнає суму, категорію і зберігає транзакцію
- **Фінансові цілі** — створення цілей із дедлайном, відстеження прогресу, автоматичний план накопичень
- **Аналіз CSV/PDF виписок** — завантажуєш виписку з банку, бот розбирає її і заносить у БД
- **AI-фінансовий радник** — відповідає на питання про твої витрати, формує щотижневий дайджест
- **Natural language parsing** — розуміє "25к", "пів мільйона", "двадцять тисяч грн"

---

## Стек

| Шар | Технологія |
|---|---|
| Bot framework | [aiogram 3.x](https://docs.aiogram.dev/) |
| LLM | [Groq API](https://console.groq.com/) — LLaMA 3.3 70B / 3.1 8B |
| Database | [Supabase](https://supabase.com/) (PostgreSQL + REST API) |
| LLM orchestration | LangChain |
| Embeddings | sentence-transformers (`all-MiniLM-L6-v2`) |
| Config | pydantic-settings |
| Scheduler | APScheduler (cron) |
| Logging | loguru |
| Deployment | Railway (worker) |

---

## Локальне встановлення

### 1. Клонуємо репозиторій

```bash
git clone https://github.com/fiks9/FinanceOS-Bot.git
cd FinanceOS-Bot
```

### 2. Створюємо та активуємо віртуальне середовище

```bash
# Windows
python -m venv .venv
.venv\Scripts\activate

# macOS / Linux
python3 -m venv .venv
source .venv/bin/activate
```

### 3. Встановлюємо залежності

```bash
pip install -r requirements.txt
```

### 4. Налаштовуємо `.env`

```bash
cp .env.example .env
```

Відкрий `.env` і заповни всі значення (детальніше — у секції нижче).

### 5. Запускаємо бота

```bash
python -m bot.run
```

---

## Конфігурація (`.env`)

```env
# Telegram
BOT_TOKEN=1234567890:AAxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx

# Groq API (https://console.groq.com → API Keys)
GROQ_API_KEY=gsk_xxxxxxxxxxxxxxxxxxxx
GROQ_MODEL_SMART=llama-3.3-70b-versatile
GROQ_MODEL_FAST=llama-3.1-8b-instant

# Supabase (Project Settings → API)
SUPABASE_URL=https://your-project-id.supabase.co
SUPABASE_SERVICE_KEY=eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9...

# App
LOG_LEVEL=INFO          # DEBUG | INFO | WARNING | ERROR
ENVIRONMENT=development # development | production
```

> **Важливо:** Використовуй `service_role` ключ для Supabase, не `anon`. Service role bypass-ує RLS — бот зможе читати/писати дані будь-якого користувача.

---

## Структура проєкту

```
FinanceOS Bot/
├── bot/
│   ├── run.py                  # Точка входу
│   ├── setup.py                # Ініціалізація Bot, Dispatcher, middleware
│   ├── config.py               # Pydantic Settings
│   ├── states.py               # FSM стани (OnboardingStates, GoalStates, ...)
│   ├── keyboards.py            # Inline/Reply клавіатури та CallbackData
│   ├── parsers.py              # Natural language парсер сум
│   ├── utils.py                # fmt_amt та інші хелпери
│   ├── fsm_storage.py          # Кастомний Supabase FSM storage
│   ├── scheduler.py            # APScheduler (weekly digest, cron)
│   ├── routers/                # aiogram роутери
│   │   ├── ai_chat.py          # Головний catchall — Intent Detection + транзакції
│   │   ├── budget.py           # /budget — фінансовий звіт + /digest
│   │   ├── goals.py            # Управління фінансовими цілями
│   │   ├── history.py          # Перегляд та редагування транзакцій
│   │   ├── onboarding.py       # Онбординг нового користувача
│   │   └── document_handler.py # Обробка CSV/PDF файлів
│   ├── handlers/
│   │   └── errors.py           # Глобальний error handler
│   ├── middlewares/
│   │   ├── auth.py             # Перевірка реєстрації користувача
│   │   └── db.py               # Ін'єкція Supabase клієнта у хендлери
│   └── services/
│       └── helpers.py          # Спільні хелпери роутерів (CONFIDENCE_THRESHOLD, _find_*)
├── ai/
│   ├── llm.py                  # ChatGroq фабрика (SMART + FAST singleton)
│   ├── intent.py               # Визначення наміру (detect_intent, extract_*)
│   ├── advisor.py              # AI-відповіді на фінансові питання
│   ├── digest.py               # Генерація weekly digest
│   ├── csv_parser.py           # Парсинг банківських CSV виписок
│   ├── pdf_parser.py           # Парсинг PDF виписок
│   └── embeddings.py           # Векторні embeddings для пошуку транзакцій
├── database/
│   ├── client.py               # Supabase async singleton
│   ├── repository.py           # Всі CRUD-запити до БД
│   └── schema.sql              # SQL-схема таблиць і VIEW
├── models/
│   └── schemas.py              # Pydantic-моделі (TransactionExtract, GoalExtract, ...)
├── .env.example
├── Procfile                    # Railway: worker: python -m bot.run
├── DEPLOY.md                   # Інструкція з деплою на Railway
├── LICENSE
└── requirements.txt
```

---

## Деплой

Детальна інструкція з деплою на Railway — у файлі **[DEPLOY.md](DEPLOY.md)**.

---

## Ліцензія

Цей проект поширюється під ліцензією [MIT](LICENSE).
