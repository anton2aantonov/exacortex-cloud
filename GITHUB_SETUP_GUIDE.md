# Private GitHub + Codex Cloud: manual connection guide

Этот файл содержит будущие шаги. Внешние подключения, git push, публикация environment и платное использование в спринте НЕ выполняются.

## 1. Проверить подготовленную локальную копию

Работать только в ExaCortex_Cloud_Ready_RC3, не в родительском C:\XAGON и не в стабильной RC3. Просмотреть GIT_INCLUDE_FILES.json и:

```sh
git status --short
git diff --cached --name-only
git diff --cached --stat
python3 -B scripts/audit_source.py --index
python3 -B scripts/cloud_checks.py
```

Репозиторий подготовлен без commit/remotes. Не использовать git add . вслепую. Если есть изменения вне allowlist — остановить staging и проверить файлы. Отчёты/test DB/.env не должны попадать в index.

## 2. Зависимости и воспроизводимость

Core и все synthetic проверки используют stdlib. Для основного suite pip install не нужен; Python3.11+/SQLite snapshot и существующий Node проверяются runtime probe. Полная команда — `python3 -B scripts/cloud_checks.py`.

Если позднее нужен изолированный Python environment, следующие команды подготовлены, но здесь не выполнялись:

```sh
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
# optional text PDF adapter, only if needed and separately permitted
.venv/bin/python -m pip install -r requirements-optional.txt
```

requirements.txt не содержит внешних пакетов; optional pypdf==6.10.0 фиксирует проверенную версию. Нет cloud SDK, torch, моделей, FFmpeg или npm install. При отсутствии venv/Node не устанавливать системные пакеты без разрешения; stdlib-проверки можно выполнить существующим Python, JS будет явно NOT TESTED при --allow-missing-node.

## 3. Отдельно разрешённая публикация

Только после review и разрешения создать PRIVATE пустой GitHub repo без начального README. Выбрать обычную авторизацию GitHub без токенов в URL/файлах. Свою identity задавать только локально в этом repo:

```sh
git config user.name "YOUR NAME"
git config user.email "YOUR NOREPLY EMAIL"
git commit -m "Prepare isolated ExaCortex cloud sources"
git remote add origin YOUR_PRIVATE_REPOSITORY_URL
git push -u origin main
```

Это НЕ команды текущего автономного спринта. Перед push проверить PRIVATE, staged contents, отсутствие .env/DB/личных данных. Не настраивать автоматические платные Actions/runners, LFS или внешние сервисы. Наличие подготовленного Git не разрешает отправку.

## 4. Codex Cloud Environment

В web/desktop: Work in → Cloud → Select environment → Create environment. Выбрать только приватный репозиторий ExaCortex и дать Codex проверить подготовку. В setup сообщить: Python3.11+, SQLite serialize/deserialize, существующий Node; команда `python3 -B scripts/cloud_checks.py`; no live AI, no media, no personal data, no automatic purchases. Никаких application API secrets не нужно. Review setup и результаты, затем Publish только после разрешения. Официальный порядок: [Cloud environments](https://learn.chatgpt.com/docs/environments/cloud-environments).

Для текущего проекта просить не устанавливать optional PDF, если не нужен: install step — `python3 -B scripts/check_runtime.py`. Start instructions — synthetic start_demo, только loopback. Выставить XAGON_ALLOW_LIVE_API=0 и PYTHONDONTWRITEBYTECODE=1, не добавлять ключи. Сетевой доступ приложения не нужен; дополнительные подключения/домены не включать. Настройки аккаунта здесь не проверялись и не менялись.

**$0 дополнительных расходов:** использовать только уже доступные возможности и включённый лимит своего аккаунта. Cloud задачи расходуют allowance; наличие папки не гарантирует бесплатный запуск. Не покупать credits, не включать overage/API billing и не менять тариф. Если включённого доступа/лимита нет — облачный этап отложить, продолжить локально. [Официальные правила usage/pricing](https://learn.chatgpt.com/docs/pricing).

## 5. Ветки, PR и возврат на компьютер

Каждая задача: отдельная feature-ветка, небольшой PR, явный список tests/NOT TESTED и review provenance. Не менять main напрямую и не выполнять force/reset/clean. Параллельным задачам нужны разные worktrees и ветки; один интегратор объединяет общие файлы.

После разрешённого merge получить изменения в Cloud-source repo через fetch/fast-forward, предварительно проверив clean working tree. Для локальной интеграции использовать НОВЫЙ checkout/worktree, а не стабильную RC3:

```sh
git fetch origin
git worktree add -b integrate/pr-123 ../ExaCortex_Integration_PR123 origin/main
# in the new integration folder
python -B scripts/cloud_checks.py
```

На Windows допускается py -3 или уже установленный разрешённый Python вместо python3. Скопировать только проверенные source changes, использовать НОВУЮ тестовую библиотеку и повторить backup/restore. Пользовательские данные не копировать из облака: их там нет. Стабильный релиз и production не заменять автоматически; после приёмки готовить новую версию. Между независимыми репозиториями нельзя переносить patches без отдельного review.
