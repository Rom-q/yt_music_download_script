#!/usr/bin/env python3
"""
Добавляет обложку ко всем музыкальным файлам в папке и копирует их в новую папку.
Запуск: python add_cover.py
Пути вводятся в консоли.
Поддержка: mp3, flac, m4a, mp4, ogg, opus, wav
"""
import base64
import shutil
from pathlib import Path

from mutagen.id3 import ID3, APIC, ID3NoHeaderError
from mutagen.flac import FLAC, Picture
from mutagen.mp4 import MP4, MP4Cover
from mutagen.oggvorbis import OggVorbis
from mutagen.oggopus import OggOpus
from mutagen.wave import WAVE

AUDIO_EXTS = {".mp3", ".flac", ".m4a", ".mp4", ".ogg", ".opus", ".wav"}


def ask(prompt: str) -> str:
    """Спросить строку, пока пользователь не введёт непустое значение."""
    while True:
        value = input(prompt).strip().strip('"').strip("'")
        if value:
            return value
        print("  ! Пустое значение, попробуйте снова.")


def ask_yes_no(prompt: str, default: bool = False) -> bool:
    suffix = "[Y/n]" if default else "[y/N]"
    while True:
        ans = input(f"{prompt} {suffix}: ").strip().lower()
        if not ans:
            return default
        if ans in {"y", "yes", "д", "да"}:
            return True
        if ans in {"n", "no", "н", "нет"}:
            return False


def get_mime(image_path: Path) -> str:
    ext = image_path.suffix.lower()
    if ext in {".jpg", ".jpeg"}:
        return "image/jpeg"
    if ext == ".png":
        return "image/png"
    raise SystemExit(f"Неподдерживаемый формат изображения: {ext}. Используйте JPG или PNG.")


def add_cover(path: Path, image_data: bytes, mime: str) -> None:
    ext = path.suffix.lower()

    if ext == ".mp3":
        try:
            tags = ID3(path)
        except ID3NoHeaderError:
            tags = ID3()
        tags.delall("APIC")
        tags.add(APIC(encoding=3, mime=mime, type=3, desc="Cover", data=image_data))
        tags.save(path)

    elif ext == ".flac":
        audio = FLAC(path)
        audio.clear_pictures()
        pic = Picture()
        pic.type = 3
        pic.mime = mime
        pic.desc = "Cover"
        pic.data = image_data
        audio.add_picture(pic)
        audio.save()

    elif ext in {".m4a", ".mp4"}:
        audio = MP4(path)
        fmt = MP4Cover.FORMAT_PNG if mime == "image/png" else MP4Cover.FORMAT_JPEG
        audio["covr"] = [MP4Cover(image_data, imageformat=fmt)]
        audio.save()

    elif ext in {".ogg", ".opus"}:
        audio = OggVorbis(path) if ext == ".ogg" else OggOpus(path)
        pic = Picture()
        pic.type = 3
        pic.mime = mime
        pic.desc = "Cover"
        pic.data = image_data
        audio["metadata_block_picture"] = [
            base64.b64encode(pic.write()).decode("ascii")
        ]
        audio.save()

    elif ext == ".wav":
        audio = WAVE(path)
        if audio.tags is None:
            audio.add_tags()
        audio.tags.delall("APIC")
        audio.tags.add(APIC(encoding=3, mime=mime, type=3, desc="Cover", data=image_data))
        audio.save()

    else:
        raise ValueError(f"Неподдерживаемый формат: {ext}")


def main():
    print("=" * 60)
    print("  Добавление обложек к музыкальным файлам")
    print("=" * 60)
    print("Подсказка: можно перетащить папку/файл прямо в это окно,")
    print("кавычки убирать не нужно — скрипт сам их отрежет.\n")

    while True:
        raw = ask("Папка с музыкой: ")
        input_dir = Path(raw).expanduser()
        if input_dir.is_dir():
            break
        print(f"  ! Не папка или не существует: {input_dir}")

    while True:
        raw = ask("Картинка обложки (.jpg/.jpeg/.png): ")
        image_path = Path(raw).expanduser()
        if not image_path.is_file():
            print(f"  ! Файл не найден: {image_path}")
            continue
        if image_path.suffix.lower() not in {".jpg", ".jpeg", ".png"}:
            print("  ! Нужен JPG или PNG.")
            continue
        break

    while True:
        raw = ask("Куда сохранить результат (новая папка): ")
        output_dir = Path(raw).expanduser()
        if output_dir.resolve() == input_dir.resolve():
            print("  ! Папка результата не должна совпадать с исходной.")
            continue
        try:
            output_dir.resolve().relative_to(input_dir.resolve())
            print("  ! Нельзя класть результат внутрь исходной папки.")
            continue
        except ValueError:
            pass
        break

    recursive = ask_yes_no("Сканировать вложенные папки?", default=False)

    print("\n--- Параметры ---")
    print(f"  Источник : {input_dir.resolve()}")
    print(f"  Обложка  : {image_path.resolve()}")
    print(f"  Результат: {output_dir.resolve()}")
    print(f"  Рекурсия : {'да' if recursive else 'нет'}")
    if not ask_yes_no("Запускаем?", default=True):
        print("Отменено.")
        return

    image_data = image_path.read_bytes()
    mime = get_mime(image_path)
    output_dir.mkdir(parents=True, exist_ok=True)

    pattern = "**/*" if recursive else "*"
    files = [
        p for p in input_dir.glob(pattern)
        if p.is_file() and p.suffix.lower() in AUDIO_EXTS
    ]

    if not files:
        print("\nАудиофайлы не найдены.")
        return

    print(f"\nНайдено файлов: {len(files)}\n")

    ok, failed = 0, 0
    for i, src in enumerate(files, 1):
        rel = src.relative_to(input_dir)
        dst = output_dir / rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        try:
            shutil.copy2(src, dst)
            add_cover(dst, image_data, mime)
            print(f"  [{i}/{len(files)}] [OK]   {rel}")
            ok += 1
        except Exception as e:
            print(f"  [{i}/{len(files)}] [FAIL] {rel}: {e}")
            failed += 1

    print("\n" + "=" * 60)
    print(f"Готово. Успешно: {ok}, Ошибок: {failed}")
    print(f"Результат в: {output_dir.resolve()}")


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n\nПрервано пользователем.")