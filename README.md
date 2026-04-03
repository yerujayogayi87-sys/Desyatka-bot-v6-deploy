# Desyatka-bot-v6-deploy

## Restore Base

В проект включён seed-файл `seed_history.json` и админская кнопка `♻️ Восстановить базу`.

После деплоя:
- открой `/admin`
- нажми `Восстановить базу`
- бот очистит игровые таблицы и загрузит seed-историю в `games`

Локально можно сделать то же самое через:

```bash
python import_history.py "seed_history.json" --truncate
```
