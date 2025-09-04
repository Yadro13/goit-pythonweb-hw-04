"""
Асинхронне сортування файлів за розширенням.

Приклад використання:
    python ht04.py -h # для довідки
    python ht04.py "C:\path\to\source" "C:\path\to\output" --max-workers 10 --log-level DEBUG --retries 5 --retry-delay 1.0 --skip-locked --exclude-glob "*/Unity/*" --exclude-glob "*/steam_autocloud.vdf"
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import os
from pathlib import Path
from typing import Iterable, Sequence

import aioshutil # pip install aioshutil


# --------------------------- CLI & Logging ---------------------------

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Асинхронне сортування файлів за розширенням (на базі aioshutil)."
    )
    parser.add_argument(
        "source",
        type=Path,
        help="Шлях до вихідної папки (source folder).",
    )
    parser.add_argument(
        "output",
        type=Path,
        help="Шлях до цільової папки (output folder).",
    )
    parser.add_argument(
        "--max-workers",
        type=int,
        default=min(32, (os.cpu_count() or 4) * 5),
        help="Макс. кількість одночасних копіювань (за замовчуванням залежить від CPU).",
    )
    parser.add_argument(
        "--log-level",
        default="INFO",
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
        help="Рівень логування.",
    )
    parser.add_argument(
        "--retries", 
        type=int, 
        default=3,       
        help="К-сть повторів для помилок копіювання (WinError 32 тощо)."
    )
    parser.add_argument(
        "--retry-delay", 
        type=float, 
        default=0.5,
        help="Початкова затримка між ретраями, сек (далі зростає ×2)."
    )
    parser.add_argument(
        "--skip-locked", 
        action="store_true",
        help="Не вважати WinError 32 помилкою, а тихо пропускати такі файли."
    )
    parser.add_argument(
        "--exclude-glob",
        action="append",
        default=[],
        help="Глоб-шаблон для виключення (можна вказувати кілька разів). Напр.: --exclude-glob '*/Unity/*' --exclude-glob '*/steam_autocloud.vdf'"
    )
    return parser


def setup_logging(level: str) -> None:
    logging.basicConfig(
        level=getattr(logging, level),
        format="%(asctime)s [%(levelname)s] %(message)s",
    )


# --------------------------- Helpers ---------------------------

# Генератор для рекурсивного обходу файлів у директорії
def iter_files_recursive(root: Path, excludes: Sequence[str]) -> Iterable[Path]:
    for p in root.rglob("*"):
        if not p.is_file():
            continue
        if excludes:
            rel = str(p.relative_to(root)).replace("\\", "/")
            skip = False
            for pat in excludes:
                if Path(rel).match(pat):
                    skip = True
                    break
            if skip:
                logging.debug("Excluded by glob %r: %s", pat, p)
                continue
        yield p

# Отримання назви папки за розширенням файлу
def ext_folder_name(path: Path) -> str:
    ext = path.suffix.lower().lstrip(".")
    return ext if ext else "no_extension"

# Отримання унікального шляху для файлу в цільовій директорії
def _unique_target_path(dst_dir: Path, filename: str) -> Path:
    candidate = dst_dir / filename
    if not candidate.exists():
        return candidate

    stem = Path(filename).stem
    suffix = Path(filename).suffix
    i = 1
    while True:
        candidate = dst_dir / f"{stem} ({i}){suffix}"
        if not candidate.exists():
            return candidate
        i += 1

# Перевірка, чи є помилка пов'язана з заблокованим файлом (WinError 32 тощо)
def _is_locked_error(exc: BaseException) -> bool:
    # На Windows locked-файл часто дає PermissionError WinError 32
    msg = str(exc)
    return isinstance(exc, PermissionError) and ("WinError 32" in msg or "process cannot access" in msg.lower())

# Асинхронне створення директорії (в окремому потоці)
async def _mkdir_async(path: Path) -> None:
    await asyncio.to_thread(path.mkdir, parents=True, exist_ok=True)

# --------------------------- Core async ops ---------------------------

# Асинхронне копіювання з ретраями та обробкою заблокованих файлів
async def copy_with_retries(src: Path, dst: Path, retries: int, delay: float, skip_locked: bool) -> tuple[bool, str | None]:
    """
    Повертає (success, reason).
    success=False і reason='locked' для пропущених через skip_locked файлів.
    success=False і reason='error' для інших помилок.
    """
    attempt = 0
    backoff = max(0.0, delay)
    while True:
        try:
            await aioshutil.copy2(src, dst)
            return True, None
        except Exception as exc:  # noqa: BLE001
            locked = _is_locked_error(exc)
            attempt += 1

            if locked and skip_locked:
                logging.warning("Пропуск (locked): %s", src)
                return False, "locked"

            if attempt > retries:
                logging.error("Помилка копіювання '%s' -> '%s' після %d спроб: %s",
                              src, dst, retries, exc, exc_info=True)
                return False, "error"

            logging.warning("Помилка копіювання '%s' (%s). Повтор #%d через %.2fs",
                            src, exc, attempt, backoff)
            await asyncio.sleep(backoff)
            backoff *= 2  # експоненційний бекоф

# Асинхронне копіювання файлу з обмеженням по кількості одночасних операцій
async def copy_file(src: Path, out_root: Path, sem: asyncio.Semaphore,
                    retries: int, delay: float, skip_locked: bool) -> tuple[bool, str | None]:
    folder = ext_folder_name(src)
    dst_dir = out_root / folder
    try:
        await _mkdir_async(dst_dir)
        target = await asyncio.to_thread(_unique_target_path, dst_dir, src.name)
        async with sem:
            return await copy_with_retries(src, target, retries, delay, skip_locked)
    except Exception as exc:  # noqa: BLE001
        logging.error("Неочікувана помилка '%s': %s", src, exc, exc_info=True)
        return False, "error"


# Рекурсивний обхід папки та планування асинхронних завдань копіювання
async def read_folder(src_root: Path, out_root: Path, max_workers: int,
                      retries: int, delay: float, skip_locked: bool,
                      excludes: Sequence[str]) -> None:
    if not src_root.exists() or not src_root.is_dir():
        raise FileNotFoundError(f"Вихідна папка не існує або не є директорією: {src_root}")

    await _mkdir_async(out_root)

    sem = asyncio.Semaphore(max_workers)
    tasks: list[asyncio.Task[tuple[bool, str | None]]] = []

    files = list(iter_files_recursive(src_root, excludes))
    if not files:
        logging.info("Файли не знайдено у: %s", src_root)
        return

    for file_path in files:
        tasks.append(asyncio.create_task(copy_file(file_path, out_root, sem, retries, delay, skip_locked)))

    logging.info("Запущено копіювань: %d (max_workers=%d)", len(tasks), max_workers)

    results = await asyncio.gather(*tasks, return_exceptions=False)

    ok = sum(1 for s, _ in results if s)
    locked = sum(1 for s, r in results if not s and r == "locked")
    failed = sum(1 for s, r in results if not s and r == "error")

    if failed or locked:
        logging.warning("Завершено: успішно=%d, пропущено locked=%d, помилок=%d", ok, locked, failed)
    else:
        logging.info("Готово без помилок. Успішно=%d", ok)


# --------------------------- Entrypoint ---------------------------

def main() -> None:
    parser = build_parser()
    args = parser.parse_args()
    setup_logging(args.log_level)

    src_root: Path = args.source
    out_root: Path = args.output

    logging.info("Source: %s", src_root)
    logging.info("Output: %s", out_root)

    try:
        asyncio.run(
            read_folder(
                src_root, out_root,
                max_workers=args.max_workers,
                retries=args.retries,
                delay=args.retry_delay,
                skip_locked=args.skip_locked,
                excludes=args.exclude_glob or [],
            )
        )
    except Exception as exc:  # noqa: BLE001
        logging.exception("Критична помилка: %s", exc)
        raise SystemExit(1) from exc


if __name__ == "__main__":
    main()