# Сайт-витрина «Maru» — руководство по редактированию и публикации

> **Это инструкция для ИИ-модели (ассистента с доступом к терминалу и файлам этого Mac).**
> Прочитай раздел 0 и 2 целиком перед любыми правками. Команды можно выполнять как есть.
> Отвечай и комментируй пользователю на русском.

---

## 0. Что это и как устроено (контекст)

**Maru** — публичная одностраничная витрина продукта (ИИ-ассистент для бизнеса) + закрытый раздел с материалами для партнёров. Уже опубликована и работает 24/7.

- **Публичный адрес:** https://business-automation.ngrok.app
- **Закрытый раздел:** https://business-automation.ngrok.app/materials/ — логин `demo`, пароль `Demo2026`
- **Где живут файлы:** `~/presentation-site/` на этом Mac (пользователь `igor`).
- **Как опубликовано:** локальный Python-сервер на `127.0.0.1:8200` (файл `app.py`) проброшен наружу через **ngrok** на домен `business-automation.ngrok.app`. Все процессы подняты через **launchd** (автозапуск + авто-рестарт, переживают перезагрузку).
- **Исходный код в GitHub:** `git@github.com:iGrKukan/maru-site.git` (приватный, ветка `main`). Этот Mac **сам подтягивает** изменения из GitHub каждую минуту (launchd-агент `com.presentation.deploy` → скрипт `deploy.sh`) и публикует их. Поэтому редактировать можно **с любого компьютера** через GitHub — доступ к маку не нужен.

> ⚠️ **Главное правило:** правки вносятся **через GitHub** (веб-редактор github.com или `git push`), НЕ ручным редактированием файлов на маке. Папка `~/presentation-site/` на маке — это «приёмник» деплоя (`git pull --ff-only`); если править её руками без коммита, авто-деплой может перестать накатываться из-за конфликта. Если всё же правишь на маке — сразу `git commit` + `git push` (см. раздел 2).

---

## 1. Карта файлов

```
~/presentation-site/
├── app.py                     # сервер: маршруты, логин, защита, форма заявки. ПРАВКА → нужен рестарт
├── .secret                    # ключ подписи сессий (НЕ трогать, НЕ публиковать)
├── EDITING_GUIDE.md           # этот файл
├── server.log / ngrok.log     # логи
└── public/                    # ← ВСЁ, что отдаётся наружу
    ├── index.html             # ГЛАВНАЯ ВИТРИНА. Здесь 95% правок контента. ПРАВКА → мгновенно
    ├── assets/                # картинки (логотип сейчас НЕ используется на странице)
    └── materials/             # закрытый раздел (под паролем)
        ├── index.html             # лендинг материалов
        ├── Automation_Deck.html   # слайды (12, листалка)
        ├── Automation_OnePager.html
        ├── Presenter_Brief.html   # внутренний бриф докладчика
        └── Deck.pdf / OnePager.pdf / Brief.pdf
```

launchd-конфиги (трогать редко): `~/Library/LaunchAgents/com.presentation.site.plist` и `com.presentation.ngrok.plist`.

---

## 2. Как опубликовать изменения — ГЛАВНОЕ

**Канонический путь (с любого компьютера, без доступа к маку):**
1. Открой репозиторий: `https://github.com/iGrKukan/maru-site`
2. Отредактируй файл (кнопка ✏️ в вебе) или `git clone` → правка → `git push` в ветку `main`.
3. Через ≤60 сек Mac сам подтянет и опубликует. Контент (`public/`) — мгновенно; при изменении `app.py` сервер авто-перезапустится.

**Если правишь прямо на маке** (модель работает на этом Mac): после правки **обязательно** закоммить и запушь, иначе авто-pull сломается:
```bash
cd ~/presentation-site && git add -A && git commit -m "что изменено" && git push
```

**Форсировать деплой сейчас (не ждать минуту):**
```bash
bash ~/presentation-site/deploy.sh
```

**Проверка, что сайт жив:**
```bash
curl -s -o /dev/null -w "локально: %{http_code}\n" http://127.0.0.1:8200/
curl -s -o /dev/null -w "публично: %{http_code}\n" https://business-automation.ngrok.app/
```
Оба должны вернуть `200`. Лог деплоя: `~/presentation-site/deploy.log`.

---

## 3. Частые задачи (рецепты)

### 3.1. Изменить текст / заголовок / карточку
Почти всё на главной — в `~/presentation-site/public/index.html`. Найди нужный текст (он на русском прямо в разметке) и поправь.

### 3.2. ⚠️ ДВУЯЗЫЧНОСТЬ (RU/EN) — читать обязательно
Сайт двуязычный. Это устроено так:
- **Русский текст** лежит прямо в HTML внутри тега с атрибутом `data-i18n="ключ"`.
- **Английский перевод** того же текста лежит в JS-словаре `const EN = { ... }` в конце файла (тег `<script>`), по тому же ключу.

**Правило: меняешь русский текст у элемента с `data-i18n` — обязательно обнови английское значение по тому же ключу в словаре `EN`.** Иначе языки разъедутся.

Пример. В HTML:
```html
<h2 data-i18n="caps.h2">Один ассистент вместо десяти программ</h2>
```
В словаре `EN`:
```js
"caps.h2":"One assistant instead of ten programs",
```

**Добавляешь НОВЫЙ переводимый текст?** Дай элементу новый ключ и добавь его в `EN`:
```html
<p data-i18n="my.newkey">Русский текст</p>
```
```js
"my.newkey":"English text",
```
Если ключа нет в `EN`, при переключении на английский элемент останется на русском (не сломается, но будет некрасиво).

Проверка EN-версии без кликов: открой `https://business-automation.ngrok.app/?lang=en` (параметр `?lang=en` / `?lang=ru` форсирует язык). Переключатель языка — кнопка EN/RU в шапке, выбор запоминается в браузере.

### 3.3. Добавить новую секцию
1. Скопируй любую существующую `<section class="block">…</section>` из `index.html` как образец.
2. Дай ей `id="..."` если нужна ссылка из меню.
3. Каждому тексту — `data-i18n="..."` + перевод в `EN` (см. 3.2).
4. Для чередования фона добавляй класс `how` (тёмная плашка) либо оставляй без него (обычный фон) — смотри, как чередуются соседние секции.
5. (Опц.) добавь пункт меню в `<div class="links">` в шапке.

### 3.4. Поменять цвета / шрифты (бренд-токены)
В начале `<style>` в `index.html` — блок `:root{ … }`. Главные переменные:
```
--accent:#d97757;   /* терракотовый акцент (фирменный) */
--bg:#1f1e1d;       /* тёмный фон */
--surface:#2b2a28;  /* карточки */
--ink:#edebe6;      /* основной текст */
--soft:#a8a59c;     /* приглушённый текст */
--line:#3a3935;     /* границы */
```
Шрифт — системный (`-apple-system…`), вордмарк «Maru» — Georgia (serif). Это фирстиль приложения Maru; менять без причины не стоит.

### 3.5. Сменить логин/пароль закрытого раздела `/materials`
В `app.py` найди:
```python
USERS = { os.environ.get("SITE_USER", "demo"): os.environ.get("SITE_PASS", "Demo2026") }
```
Поменяй `"demo"` и `"Demo2026"` (пароль ≥ 1 символа, но делай надёжный). Затем рестарт сервера:
```bash
launchctl kickstart -k gui/$(id -u)/com.presentation.site
```
В системе есть защита от перебора: 5 неверных попыток → блок IP на 15 минут.

### 3.6. Обновить материалы (слайды / бриф / one-pager / PDF)
Файлы в `public/materials/`. HTML-исходники слайдов/брифа/one-pager там же. Пересобрать PDF из HTML (headless Chrome):
```bash
CH="/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
"$CH" --headless --disable-gpu --no-pdf-header-footer \
  --print-to-pdf="$HOME/presentation-site/public/materials/OnePager.pdf" \
  "file://$HOME/presentation-site/public/materials/Automation_OnePager.html"
```
Слайды (`Automation_Deck.html`) — это листалка (по одному слайду на экран); для корректного PDF нужна печатная копия с правилами `@media print`, делающими каждый слайд отдельной альбомной страницей (в Deck.pdf это уже учтено — при пересборке добавь такой `<style media=print>`).

### 3.7. Сменить домен / имя сайта
Домен `.ngrok.app` минтится на лету. В `~/Library/LaunchAgents/com.presentation.ngrok.plist` поменяй аргумент `--url=https://business-automation.ngrok.app` на новый (например `--url=https://maru-demo.ngrok.app`), затем:
```bash
launchctl kickstart -k gui/$(id -u)/com.presentation.ngrok
sleep 4
grep -o '"url":"https://[^"]*"' ~/presentation-site/ngrok.log | tail -1   # узнать актуальный URL
```
Имя должно быть глобально свободным во всём ngrok. Не занимай домены, уже используемые на этой машине: `maru.ngrok.app` и `oink-morale-wham.ngrok-free.dev` заняты другими сервисами — не трогать.

### 3.8. Форма заявки «Запросить демо»
Заявка с сайта (`POST /api/lead`) уходит в **Telegram** через скрипт `~/openclaw-server/scripts/tools/tg_send.py` получателю `obiwan`. Чтобы сменить получателя — в `app.py` поменяй `LEAD_TO = "obiwan"` (имя агента из `~/maru-bot/config.json` или числовой chat_id) и рестартни сервер. Антиспам: ≤5 заявок с одного IP в час.

---

## 4. Проверка и отладка

```bash
# статус launchd-сервисов (ищи "state = running")
launchctl print gui/$(id -u)/com.presentation.site | grep -E 'state|pid'
launchctl print gui/$(id -u)/com.presentation.ngrok | grep -E 'state|pid'

# логи
tail -n 30 ~/presentation-site/server.log
tail -n 30 ~/presentation-site/ngrok.log

# скриншот для визуальной проверки вёрстки (RU и EN)
CH="/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
"$CH" --headless --disable-gpu --hide-scrollbars --window-size=1280,4000 \
  --screenshot=/tmp/check_ru.png "http://127.0.0.1:8200/"
"$CH" --headless --disable-gpu --hide-scrollbars --window-size=1280,4000 \
  --screenshot=/tmp/check_en.png "http://127.0.0.1:8200/?lang=en"
```

Если публичный URL не отвечает, а локальный (`127.0.0.1:8200`) отвечает — проблема в ngrok: `launchctl kickstart -k gui/$(id -u)/com.presentation.ngrok` и проверь `ngrok.log`.

---

## 5. Подводные камни (НЕ делай так)

- ❌ Не редактируй текст, забыв про английский перевод (см. 3.2) — языки разъедутся.
- ❌ Не клади приватное (`app.py`, `.secret`, этот гайд) в папку `public/` — она вся доступна снаружи (раздел `/materials` под паролем, остальное публично).
- ❌ Не удаляй `.secret` — это инвалидирует все активные сессии входа (не критично, просто всем придётся перелогиниться).
- ❌ Не занимай ngrok-домены `maru.ngrok.app` и `oink-morale-wham.ngrok-free.dev` — заняты рабочими сервисами.
- ❌ Не запускай второй `python http.server` или второй ngrok на порт 8200 вручную — этим управляет launchd; для перезапуска используй `launchctl kickstart -k`.
- ✅ После правок `app.py` — всегда рестарт + `curl` проверка.
- ✅ Контент-правки в `public/` — мгновенно, рестарт НЕ нужен.

---

## 6. Если переносить сайт на другую машину (кратко)

1. Скопировать всю папку `~/presentation-site/` (включая `public/`, `app.py`, `.secret`).
2. Поставить Python 3 (на macOS — системный `/usr/bin/python3` подходит) и ngrok (`brew install ngrok`, прописать свой `ngrok config add-authtoken …`).
3. Запустить сервер: `cd ~/presentation-site && python3 app.py` (слушает `127.0.0.1:8200`).
4. Запустить туннель: `ngrok http 8200 --url=https://<своё-имя>.ngrok.app`.
5. Для автозапуска — перенести два plist из `~/Library/LaunchAgents/` (поправив пути под нового пользователя) и `launchctl bootstrap gui/$(id -u) <plist>`.

---

## Сводка одной строкой
Правишь `public/index.html` (тексты + перевод в словаре `EN`) → обновляешь браузер. Меняешь `app.py` → `launchctl kickstart -k gui/$(id -u)/com.presentation.site`. Сайт уже живёт на https://business-automation.ngrok.app.
