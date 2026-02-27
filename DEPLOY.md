# Інструкція з деплою

## 1. GitHub — перший пуш

### Ініціалізація репозиторію

```bash
# Переходимо в папку проєкту (якщо ще не там)
cd "FinanceOS Bot"

# Ініціалізуємо git
git init

# Переконуємось, що .gitignore на місці (це критично, щоб не пушити .env!)
cat .gitignore

# Додаємо всі файли (крім тих, що в .gitignore)
git add .

# Перевіряємо, що .env НЕ потрапив у staged files
git status
# Якщо .env є в списку — СТОП, перевір .gitignore

# Перший коміт
git commit -m "feat: initial commit — FinanceOS Bot"
```

### Підключення до GitHub і пуш

```bash
# Заміни YOUR_USERNAME на своє ім'я на GitHub
git remote add origin https://github.com/fiks9/finance-os-bot.git

# Перейменовуємо гілку (якщо git ще на master)
git branch -M main

# Пушимо
git push -u origin main
```

> Якщо репозиторій на GitHub ще не створений — спочатку створи його на [github.com/new](https://github.com/new). **Без** галочки "Initialize with README" (ми вже маємо свій).

---

### Подальші пуші (звичайний workflow)

```bash
git add .
git commit -m "fix: описи змін"
git push
```

---

## 2. Railway — деплой

### 2.1. Створення проєкту

1. Заходимо на [railway.app](https://railway.app) → **New Project**
2. Обираємо **Deploy from GitHub repo**
3. Авторизуємо GitHub і вибираємо репозиторій `finance-os-bot`
4. Railway автоматично виявить `Procfile` і налаштує білд

### 2.2. Додавання змінних середовища

1. Відкрий свій проєкт на Railway
2. Зліва вибери свій сервіс → вкладка **Variables**
3. Натисни **Raw Editor** і встав:

```
BOT_TOKEN=ВАШ_ТОКЕН_БОТА
GROQ_API_KEY=ВАШ_GROQ_КЛЮЧ
GROQ_MODEL_SMART=llama-3.3-70b-versatile
GROQ_MODEL_FAST=llama-3.1-8b-instant
SUPABASE_URL=https://your-project-id.supabase.co
SUPABASE_SERVICE_KEY=ВАШ_SERVICE_ROLE_КЛЮЧ
LOG_LEVEL=INFO
ENVIRONMENT=production
```

4. Натисни **Update Variables** — Railway автоматично перезапустить сервіс

### 2.3. Start Command

Railway підхопить `Procfile` автоматично. У ньому вже прописано:

```
worker: python -m bot.run
```

> **Важливо:** Бот працює як **worker**, а не web-сервіс. Не потрібно відкривати порт чи налаштовувати HTTP. Long Polling сам підключається до Telegram.

Якщо хочеш вказати команду вручну (у Settings → Deploy → Start Command):

```
python -m bot.run
```

### 2.4. Перевірка деплою

1. Вкладка **Deployments** — переконайся, що білд пройшов (зелена позначка ✓)
2. Вкладка **Logs** — має з'явитись:
   ```
   Starting FinanceOS Bot | env=production
   Supabase client initialized ✓
   Scheduler started ✓
   Bot started polling...
   ```
3. Напиши `/start` своєму боту в Telegram — він має відповісти

### 2.5. Типові проблеми

| Симптом | Причина | Рішення |
|---|---|---|
| `ValidationError` при старті | Не додано змінну середовища | Перевір Variables на Railway, всі 6 ключів мають бути заповнені |
| `TelegramUnauthorizedError` | Невірний `BOT_TOKEN` | Перевір токен у @BotFather |
| `postgrest.exceptions.APIError` | Невірний Supabase ключ або URL | Перевір `SUPABASE_URL` і `SUPABASE_SERVICE_KEY` |
| Білд падає на `sentence-transformers` | Timeout при завантаженні моделі | Нормально для першого deply — Railway дає 10 хв на білд |

---

## 3. Оновлення після змін у коді

```bash
# Локально вносимо зміни, потім:
git add .
git commit -m "feat: нова фіча"
git push
```

Railway автоматично виявить новий коміт і перезапустить деплой. Звичайно займає 2–3 хвилини.
