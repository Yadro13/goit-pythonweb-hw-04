\# HT04 — Async File Sorter (aioshutil)



Асинхронний сортувальник файлів за розширеннями. Рекурсивно читає вихідну папку і копіює файли в підпапки цільової директорії (`jpg/`, `pdf/`, `no\_extension/` тощо). Працює з \*\*`aioshutil`\*\*, підтримує \*\*ретраї\*\*, \*\*тихий пропуск locked-файлів\*\* (WinError 32), та \*\*exclude-глоби\*\* для шумних кешів (Unity, Steam і т.п.).



\## Особливості

\- Асинхронне копіювання файлів (`aioshutil.copy2`) із обмеженням паралельності.

\- Ретраї з експоненційним бекофом: `--retries`, `--retry-delay`.

\- Пропуск файлів, зайнятих іншими процесами: `--skip-locked`, та тихий режим `--silent-locked`.

\- Фільтрування джерела за glob-шаблонами: `--exclude-glob` (можна кілька разів).

\- Уникнення колізій імен: автоматичні суфікси `(<n>)`.



\## Встановлення



\### Через `requirements.txt`

```bash

python -m venv .venv

. .venv/bin/activate  # Windows: .venv\\Scripts\\activate

pip install -r requirements.txt



# goit-pythonweb-hw-04
