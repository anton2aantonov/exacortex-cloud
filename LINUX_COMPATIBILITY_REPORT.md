# Linux compatibility — NOT TESTED on native Linux

## Что доступно

Хост Windows; проверен Python3.12.14/SQLite3.53.1. WSL `--list --quiet` и `--list` вернули usage help и код1, рабочий дистрибутив не подтверждён. Docker/Bash не найдены в PATH. Обнаруженный Git Bash позволяет `--noprofile --norc -n scripts/test_offline.sh`, но это Windows/MSYS, не Linux. WSL/Docker/ядро/дистрибутивы не устанавливались и не запускались.

## Windows-зависимые участки

| Участок | Характер зависимости и решение |
|---|---|
| learning.py: LOCALAPPDATA/AppData/Store-Python roots | Защитные checks прежнего production-хранилища. На Linux укажите нативный EXACORTEX_LEARNING_DIR; стандартная рабочая папка — repository-local rc_data. XAGON_DATA_DIR защищается на всех платформах. Никакого чтения реального Windows production для тестов нет. |
| rc_store.py / knowledge_store.py / fusion_store.py / intelligence_store.py | Windows C:\XAGON archive blacklists. RC guard теперь условный Windows и включает стабильную RC3; другие старые guards не создают эти каталоги. Они не заменяют POSIX root policy. |
| learning.py: входные Windows drive paths | На POSIX отклоняются до создания БД, чтобы C:\... не превратился в относительное имя. POSIX-ветка пока SKIP на Windows. |
| rc_backup.py: SQLite deserialize/setlimit и optional is_junction | Нужен Python3.11+ и SQLite build с serialize/deserialize. Junction check условный через hasattr; symlink/resolve checks работают независимо. Runtime probe проверяет возможности, а не предполагает их наличие. |
| alpha.py: webbrowser | Для Cloud запуск только --no-browser / start_demo; bind127.0.0.1, не 0.0.0.0. Локальный браузер Windows в Linux не переносится. |
| shell / Node / кодировки | .sh/.py/.js в UTF-8/LF, .gitattributes закрепляет LF. Команды используют python3 и node из среды, не Windows Store alias и не USERPROFILE. Нет npm-зависимостей. |

## Подтверждено на Windows

Unicode/пробелы в путях, точный UTF-8 текст после reopen, FTS5, foreign keys, независимость двух store, backup/restore в Unicode-каталог, сохранение истории, syntax всех Python файлов и shell LF. Bash syntax проверен без профилей. Реальные Linux semantics, file permissions/case sensitivity, POSIX drive rejection, signals и Linux sockets пока NOT TESTED.

## Обязательная Linux-приёмка позже

В уже доступной разрешённой Linux/Cloud-среде выполнить:

```sh
python3 -B scripts/check_runtime.py
python3 -B scripts/audit_source.py
python3 -B scripts/cloud_checks.py
sh scripts/test_offline.sh
python3 -B scripts/start_demo.py --directory .test-data/linux-demo-NEW --port 8765
```

Ожидается 22 unit cases без Windows-only skip, 25 HTTP assertions и28 JS assertions при доступном Node. Проверить SIGINT/завершение demo, режимы файлов, регистр имён, slow/disconnect/identity transport, restore. Если SQLite snapshot/Node отсутствуют — зафиксировать блокировку, не устанавливать системные компоненты автоматически. Ветку Linux-specific до успешного прогона не объявлять принятой.
