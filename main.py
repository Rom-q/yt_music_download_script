#!/usr/bin/env python3
"""
единый интерфейс для:
  • скачивания с YouTube              (yt_downloader.py)
  • записи метаданных Spotify/YouTube (metadata_lib.py)
  • встраивания обложек               (cover_lib.py)

"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import metadata_lib
import cover_lib

from yt_downloader import (
    DOWNLOAD_DIR,
    ask_audio_quality,
    collect_urls,
    download_all,
    ensure_download_dir,
    preview_urls,
)
from metadata_lib import (
    AUDIO_EXTS,
    ConfigError,
    MetadataError,
    SourceFetchError,
    fetch_spotify,
    fetch_youtube,
    match_file_to_track,
    process_folder as process_metadata,
    write_tags,
)
from cover_lib import (
    CoverError,
    add_cover,
    guess_mime,
    process_folder as process_covers,
)


CANCEL_WORDS = {"0", "отмена", "cancel", "выход", "exit", "q", "назад"}


def _clean(s: str) -> str:
    return s.strip().strip('"').strip("'")


def _is_cancel(s: str) -> bool:
    return s.lower() in CANCEL_WORDS


def ask_text(prompt: str, default: str | None = None) -> str | None:
    """Спрашивает строку. None = отмена. Пустой ввод = default (если есть)."""
    while True:
        raw = _clean(input(prompt))
        if _is_cancel(raw):
            print("  ↩ Отменено.")
            return None
        if not raw:
            if default is not None:
                return default
            print("  ! Пустое значение. Введите заново (или 'отмена').")
            continue
        return raw


def ask_dir(prompt: str, must_exist: bool = True) -> Path | None:
    while True:
        raw = _clean(input(prompt))
        if _is_cancel(raw):
            print("  ↩ Отменено.")
            return None
        if not raw:
            print("  ! Пустое значение. Введите заново (или 'отмена').")
            continue
        p = Path(raw).expanduser()
        if must_exist and not p.is_dir():
            print(f"  ! Не папка или не существует: {p}")
            continue
        return p


def ask_file(prompt: str, exts: set[str] | None = None) -> Path | None:
    while True:
        raw = _clean(input(prompt))
        if _is_cancel(raw):
            print("  ↩ Отменено.")
            return None
        if not raw:
            print("  ! Пустое значение. Введите заново (или 'отмена').")
            continue
        p = Path(raw).expanduser()
        if not p.is_file():
            print(f"  ! Файл не найден: {p}")
            continue
        if exts and p.suffix.lower() not in exts:
            print(f"  ! Нужен один из форматов: {', '.join(sorted(exts))}")
            continue
        return p


def ask_yes_no(prompt: str, default: bool = False) -> bool | None:
    """None = отмена."""
    suffix = "[Y/n]" if default else "[y/N]"
    while True:
        a = _clean(input(f"{prompt} {suffix}: ")).lower()
        if _is_cancel(a):
            print("  ↩ Отменено.")
            return None
        if not a:
            return default
        if a in {"y", "yes", "д", "да"}:
            return True
        if a in {"n", "no", "н", "нет"}:
            return False
        print("  ! Введите y или n (или 'отмена').")


def ask_choice(prompt: str, valid: set[str]) -> str | None:
    while True:
        raw = _clean(input(prompt))
        if _is_cancel(raw):
            print("  ↩ Отменено.")
            return None
        if raw in valid:
            return raw
        print(f"  ! Допустимо: {', '.join(sorted(valid))} (или 'отмена').")


def ask_output_dir(input_dir: Path) -> Path | None:
    """Спрашивает папку результата. Гарантирует, что она не внутри input_dir."""
    while True:
        p = ask_dir("Куда сохранить результат: ", must_exist=False)
        if p is None:
            return None
        try:
            if p.resolve() == input_dir.resolve():
                print("  ! Папка результата не должна совпадать с исходной.")
                continue
        except OSError:
            pass
        try:
            p.resolve().relative_to(input_dir.resolve())
            print("  ! Нельзя класть результат внутрь исходной папки.")
            continue
        except ValueError:
            pass
        return p


def _yt_downloader_command() -> list[str]:
    """Команда для запуска yt_downloader."""
    if getattr(sys, "frozen", False):
        return [sys.executable, "--yt-downloader"]
    here = Path(__file__).resolve().parent
    return [sys.executable, str(here / "yt_downloader.py")]


def launch_youtube_downloader() -> None:
    """Открывает окно загрузчика YouTube в отдельном процессе."""
    cmd = _yt_downloader_command()

    creationflags = 0
    if sys.platform == "win32":
        creationflags = subprocess.CREATE_NEW_CONSOLE

    try:
        subprocess.Popen(cmd, creationflags=creationflags)
        print("\n  ➜ Загрузчик открылся в отдельном окне.")
    except Exception as e:
        print(f"  ! Не удалось запустить загрузчик: {e}")


def print_main_menu() -> None:
    print()
    print("=" * 60)
    print("                main.py")
    print("=" * 60)
    print("  1. Скачать с YouTube")
    print("  2. Записать метаданные (Spotify / YouTube)")
    print("  3. Добавить обложку из файла")
    print("  4. Полный цикл: скачать → метаданные → обложка")
    print("  0. Выход")
    print("=" * 60)


def _fetch_tracks_interactive() -> list[metadata_lib.TrackMeta] | None:
    while True:
        src = ask_choice(
            "Источник метаданных (1=Spotify, 2=YouTube): ",
            valid={"1", "2"},
        )
        if src is None:
            return None

        if src == "1":
            url = ask_text("Ссылка на альбом/трек/плейлист Spotify: ")
            if url is None:
                return None
            print("\nЗагружаю из Spotify...")
            try:
                return fetch_spotify(url)
            except (ConfigError, SourceFetchError, MetadataError) as e:
                print(f"  ! {e}")
                if not ask_yes_no("Попробовать снова?", default=True):
                    return None

        else:  # src == 2
            url = ask_text("Ссылка на видео/плейлист YouTube: ")
            if url is None:
                return None
            print("\nЗагружаю из YouTube...")
            try:
                return fetch_youtube(url)
            except (SourceFetchError, MetadataError) as e:
                print(f"  ! {e}")
                if not ask_yes_no("Попробовать снова?", default=True):
                    return None


def _ask_duration_tolerance() -> float | None:
    """Спрашивает допуск по длительности. None = отмена."""
    while True:
        raw = ask_text("Допуск по длительности, сек (например 3): ")
        if raw is None:
            return None
        try:
            v = float(raw.replace(",", "."))
            if v > 0:
                return v
        except ValueError:
            pass
        print("  ! Введите положительное число.")


def flow_metadata() -> None:
    tracks = _fetch_tracks_interactive()
    if not tracks:
        return

    print(f"\nНайдено треков: {len(tracks)}")
    for t in tracks[:5]:
        print(f"  • {t.artist or '?'} — {t.title or '?'}")
    if len(tracks) > 5:
        print(f"  ... и ещё {len(tracks) - 5}")

    input_dir = ask_dir("\nПапка с музыкой: ")
    if input_dir is None:
        return

    output_dir = ask_output_dir(input_dir)
    if output_dir is None:
        return

    recursive = ask_yes_no("Сканировать вложенные папки?", default=False)
    if recursive is None:
        return

    use_duration = ask_yes_no("Сопоставлять ещё и по длительности?", default=False)
    if use_duration is None:
        return

    tolerance = 0.0
    if use_duration:
        tolerance = _ask_duration_tolerance()
        if tolerance is None:
            return

    if not ask_yes_no("Запускаем?", default=True):
        print("Отменено.")
        return

    result = process_metadata(
        input_dir, output_dir, tracks,
        recursive=recursive,
        duration_tolerance=tolerance,
    )
    print("\n" + "=" * 60)
    print(f"Готово. Тегировано: {result.ok}, "
          f"пропущено: {result.skipped}, ошибок: {result.failed}")
    print(f"Результат в: {output_dir.resolve()}")


def flow_cover() -> None:
    input_dir = ask_dir("Папка с музыкой: ")
    if input_dir is None:
        return

    image_path = ask_file("Картинка обложки (.jpg/.jpeg/.png): ",
                          exts={".jpg", ".jpeg", ".png"})
    if image_path is None:
        return

    output_dir = ask_output_dir(input_dir)
    if output_dir is None:
        return

    recursive = ask_yes_no("Сканировать вложенные папки?", default=False)
    if recursive is None:
        return

    if not ask_yes_no("Запускаем?", default=True):
        print("Отменено.")
        return

    try:
        result = process_covers(
            input_dir, image_path, output_dir, recursive=recursive,
        )
    except CoverError as e:
        print(f"  ! {e}")
        return

    print("\n" + "=" * 60)
    print(f"Готово. Успешно: {result.ok}, ошибок: {result.failed}")
    print(f"Результат в: {output_dir.resolve()}")


def flow_full_pipeline() -> None:
    print("\n" + "=" * 60)
    print("Полный цикл: скачать с YouTube + метаданные")
    print("=" * 60)

    urls = collect_urls()
    if not urls:
        return
    preview_urls(urls)

    audio_quality = ask_audio_quality()

    if not ask_yes_no("Начать скачивание?", default=True):
        print("Отменено.")
        return

    ensure_download_dir()
    download_path = Path(DOWNLOAD_DIR)
    before = {p.resolve() for p in download_path.glob("*") if p.is_file()}

    download_all(
        urls, mode="2", video_fmt="bestvideo",
        audio_quality=audio_quality, use_playlist=True,
    )

    after = {p.resolve() for p in download_path.glob("*") if p.is_file()}
    new_files = sorted(after - before)

    if not new_files:
        print("\nНовых файлов не появилось.")
        return

    print(f"\nСкачано файлов: {len(new_files)}")

    print("\nОткуда взять метаданные?")
    print("  1. Spotify")
    print("  2. YouTube (те же ссылки)")
    print("  3. Пропустить")
    meta_choice = ask_choice("Ваш выбор [3]: ", valid={"1", "2", "3"}) or "3"

    tracks: list[metadata_lib.TrackMeta] = []

    if meta_choice == "1":
        spotify_url = ask_text("Ссылка Spotify: ")
        if spotify_url:
            print("\nЗагружаю метаданные из Spotify...")
            try:
                tracks = fetch_spotify(spotify_url)
            except (ConfigError, SourceFetchError, MetadataError) as e:
                print(f"  ! {e}")
    elif meta_choice == "2":
        print("\nЗагружаю метаданные из YouTube...")
        for u in urls:
            try:
                tracks.extend(fetch_youtube(u))
            except SourceFetchError as e:
                print(f"  ! {u}: {e}")

    if tracks:
        print(f"\nНайдено треков: {len(tracks)}")

        use_dur = ask_yes_no(
            "Сопоставлять дополнительно по длительности?", default=False
        )
        tolerance = 0.0
        if use_dur:
            tolerance = _ask_duration_tolerance() or 0.0

        ok = skipped = failed = 0
        for f in new_files:
            fp = Path(f)
            if fp.suffix.lower() not in AUDIO_EXTS:
                continue
            meta = match_file_to_track(fp, tracks,
                                       duration_tolerance=tolerance)
            if not meta:
                print(f"  [SKIP] {fp.name}  (нет совпадения)")
                skipped += 1
                continue
            try:
                write_tags(fp, meta)
                mark = " +обложка" if meta.cover_data else ""
                print(f"  [OK]   {fp.name}  →  "
                      f"{meta.artist or '?'} — {meta.title}{mark}")
                ok += 1
            except Exception as e:
                print(f"  [FAIL] {fp.name}: {e}")
                failed += 1

        print("\n" + "=" * 60)
        print(f"Тегировано: {ok}, пропущено: {skipped}, ошибок: {failed}")
    else:
        print("Метаданные не получены — файлы остались без тегов.")

    if ask_yes_no(
        "\nДобавить ещё обложку из отдельного файла поверх всех?",
        default=False,
    ):
        image_path = ask_file("Картинка (.jpg/.jpeg/.png): ",
                              exts={".jpg", ".jpeg", ".png"})
        if image_path:
            try:
                data = image_path.read_bytes()
                mime = guess_mime(image_path)
                applied = 0
                for f in new_files:
                    fp = Path(f)
                    if fp.suffix.lower() in AUDIO_EXTS:
                        try:
                            add_cover(fp, data, mime)
                            applied += 1
                        except Exception as e:
                            print(f"  ! {fp.name}: {e}")
                print(f"Обложка встроена в {applied} файл(ов).")
            except CoverError as e:
                print(f"  ! {e}")

    print(f"\nФайлы: {download_path.resolve()}")



def _run_youtube_downloader_cli() -> int:
    """Точка входа дочернего процесса — чистый yt_downloader."""
    import yt_downloader
    try:
        yt_downloader.main()
    except KeyboardInterrupt:
        print("\n\nПрервано пользователем.")
    return 0


def main() -> int:
    print("=" * 60)
    print("  Добро пожаловать")
    print("=" * 60)
    print("  Модули: yt_downloader • metadata_lib • cover_lib")
    print("  Подсказка: в любом вопросе можно ввести «отмена» / 0,")
    print("             чтобы вернуться в меню.")

    while True:
        print_main_menu()
        choice = input("Выберите пункт: ").strip()

        if choice == "0":
            return 0
        elif choice == "1":
            launch_youtube_downloader()
        elif choice == "2":
            flow_metadata()
        elif choice == "3":
            flow_cover()
        elif choice == "4":
            flow_full_pipeline()
        else:
            print("Неверный выбор. Введите 0-4.")


if __name__ == "__main__":
    if "--yt-downloader" in sys.argv:
        sys.exit(_run_youtube_downloader_cli())

    try:
        sys.exit(main())
    except KeyboardInterrupt:
        print("\n\nПрервано пользователем.")
        sys.exit(130)