import base64
import shutil
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Iterable

from mutagen.id3 import ID3, APIC, ID3NoHeaderError
from mutagen.flac import FLAC, Picture
from mutagen.mp4 import MP4, MP4Cover
from mutagen.oggvorbis import OggVorbis
from mutagen.oggopus import OggOpus
from mutagen.wave import WAVE

AUDIO_EXTS = {".mp3", ".flac", ".m4a", ".mp4", ".ogg", ".opus", ".wav"}
IMAGE_EXTS = {".jpg", ".jpeg", ".png"}



class CoverError(Exception):
    """Базовая ошибка."""

class UnsupportedFormatError(CoverError):
    """Расширение не поддерживается."""

class BadImageError(CoverError):
    """Проблема с файлом картинки."""



@dataclass
class FileResult:
    source: Path
    target: Path
    ok: bool
    error: str | None = None


@dataclass
class ProcessResult:
    total: int = 0
    ok: int = 0
    failed: int = 0
    files: list[FileResult] = field(default_factory=list)



def guess_mime(image_path: Path) -> str:
    ext = image_path.suffix.lower()
    if ext in {".jpg", ".jpeg"}:
        return "image/jpeg"
    if ext == ".png":
        return "image/png"
    raise BadImageError(f"Неподдерживаемый формат картинки: {ext}. Нужен JPG или PNG.")


def add_cover(audio_path: Path, image_data: bytes, mime: str) -> None:
    """Встроить обложку в один аудиофайл. Бросает исключение при ошибке."""
    ext = audio_path.suffix.lower()

    if ext == ".mp3":
        try:
            tags = ID3(audio_path)
        except ID3NoHeaderError:
            tags = ID3()
        tags.delall("APIC")
        tags.add(APIC(encoding=3, mime=mime, type=3, desc="Cover", data=image_data))
        tags.save(audio_path)

    elif ext == ".flac":
        audio = FLAC(audio_path)
        audio.clear_pictures()
        pic = Picture()
        pic.type = 3
        pic.mime = mime
        pic.desc = "Cover"
        pic.data = image_data
        audio.add_picture(pic)
        audio.save()

    elif ext in {".m4a", ".mp4"}:
        audio = MP4(audio_path)
        fmt = MP4Cover.FORMAT_PNG if mime == "image/png" else MP4Cover.FORMAT_JPEG
        audio["covr"] = [MP4Cover(image_data, imageformat=fmt)]
        audio.save()

    elif ext in {".ogg", ".opus"}:
        audio = OggVorbis(audio_path) if ext == ".ogg" else OggOpus(audio_path)
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
        audio = WAVE(audio_path)
        if audio.tags is None:
            audio.add_tags()
        audio.tags.delall("APIC")
        audio.tags.add(APIC(encoding=3, mime=mime, type=3, desc="Cover", data=image_data))
        audio.save()

    else:
        raise UnsupportedFormatError(f"Неподдерживаемый формат: {ext}")



ProgressCallback = Callable[[int, int, Path], None]


def process_folder(
    input_dir: Path | str,
    image_path: Path | str,
    output_dir: Path | str,
    recursive: bool = False,
    on_progress: ProgressCallback | None = None,
    cancel_flag: Callable[[], bool] | None = None,
) -> ProcessResult:
    """
    :param input_dir:    папка с музыкой
    :param image_path:   картинка обложки
    :param output_dir:   куда копировать результат
    :param recursive:    заходить ли во вложенные папки
    :param on_progress:  колбэк (i, total, file_path) — для прогресс-бара
    :param cancel_flag:  функция без аргументов; если вернёт True — остановиться
    :return:             ProcessResult со статистикой и списком файлов
    """
    input_dir = Path(input_dir).expanduser()
    output_dir = Path(output_dir).expanduser()
    image_path = Path(image_path).expanduser()

    if not input_dir.is_dir():
        raise CoverError(f"Не папка: {input_dir}")
    if not image_path.is_file():
        raise BadImageError(f"Картинка не найдена: {image_path}")

    image_data = image_path.read_bytes()
    mime = guess_mime(image_path)
    output_dir.mkdir(parents=True, exist_ok=True)

    pattern = "**/*" if recursive else "*"
    files = sorted(
        p for p in input_dir.glob(pattern)
        if p.is_file() and p.suffix.lower() in AUDIO_EXTS
    )

    result = ProcessResult(total=len(files))

    for i, src in enumerate(files, 1):
        if cancel_flag and cancel_flag():
            break

        rel = src.relative_to(input_dir)
        dst = output_dir / rel
        dst.parent.mkdir(parents=True, exist_ok=True)

        try:
            shutil.copy2(src, dst)
            add_cover(dst, image_data, mime)
            result.files.append(FileResult(src, dst, ok=True))
            result.ok += 1
        except Exception as e:
            result.files.append(FileResult(src, dst, ok=False, error=str(e)))
            result.failed += 1

        if on_progress:
            on_progress(i, len(files), src)

    return result