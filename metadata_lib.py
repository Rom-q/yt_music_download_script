"""
Библиотека для записи метаданных из Spotify/YouTube в аудиофайлы.

Все ошибки — через исключения или в ProcessResult.
Прогресс — через колбэк on_progress.
Отмена — через колбэк cancel_flag.
"""
from __future__ import annotations

import base64
import re
import shutil
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Iterable

import requests
import spotipy
from spotipy.oauth2 import SpotifyClientCredentials
import yt_dlp

from mutagen import File as MutagenFile
from mutagen.id3 import (
    ID3, TIT2, TPE1, TALB, TDRC, TRCK, TPE2, APIC, ID3NoHeaderError,
)
from mutagen.flac import FLAC, Picture
from mutagen.mp4 import MP4, MP4Cover
from mutagen.oggvorbis import OggVorbis
from mutagen.oggopus import OggOpus
from mutagen.wave import WAVE


AUDIO_EXTS: frozenset[str] = frozenset(
    {".mp3", ".flac", ".m4a", ".mp4", ".ogg", ".opus", ".wav"}
)


class MetadataError(Exception):
    """Базовая ошибка библиотеки."""

class SourceFetchError(MetadataError):
    """Не удалось получить метаданные из Spotify/YouTube."""

class ConfigError(MetadataError):
    """Не хватает ключей Spotify."""

class UnsupportedFormatError(MetadataError):
    """Расширение аудиофайла не поддерживается."""


@dataclass
class TrackMeta:
    """Метаданные одного трека."""
    title: str | None = None
    artist: str | None = None
    album: str | None = None
    album_artist: str | None = None
    year: str | None = None
    track_number: int | None = None
    total_tracks: int | None = None
    cover_data: bytes | None = None
    cover_mime: str | None = None
    source_id: str | None = None
    duration: float | None = None

    def normalized_title(self) -> str:
        return normalize(self.title or "")

@dataclass
class FileResult:
    """Результат обработки одного файла."""
    source: Path
    target: Path
    ok: bool
    matched: str | None = None
    error: str | None = None

@dataclass
class ProcessResult:
    """Итог обработки папки."""
    total: int = 0
    ok: int = 0
    skipped: int = 0
    failed: int = 0
    cancelled: bool = False
    files: list[FileResult] = field(default_factory=list)

ProgressCallback = Callable[[int, int, Path], None]   # (i, total, path)
CancelFlag = Callable[[], bool]


def normalize(s: str) -> str:
    """Нормализует строку для нечёткого сравнения названий."""
    s = unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode()
    s = s.lower()
    s = re.sub(r"[\(\[].*?[\)\]]", "", s)
    s = re.sub(r"[^a-z0-9]+", " ", s)
    return re.sub(r"\s+", " ", s).strip()

def _download_image(url: str | None, timeout: int = 15) -> tuple[bytes, str] | None:
    """Скачивает картинку. Возвращает (data, mime) или None."""
    if not url:
        return None
    try:
        r = requests.get(url, timeout=timeout)
        r.raise_for_status()
        mime = r.headers.get("Content-Type", "image/jpeg").split(";")[0].strip()
        if mime not in {"image/jpeg", "image/png"}:
            mime = "image/jpeg"
        return r.content, mime
    except Exception:
        return None

def get_audio_duration(path: Path) -> float | None:
    """Возвращает длительность аудиофайла в секундах или None."""
    try:
        f = MutagenFile(path)
        if f is not None and getattr(f, "info", None) is not None:
            length = getattr(f.info, "length", None)
            if length:
                return float(length)
    except Exception:
        pass
    return None


def fetch_spotify(
    url_or_id: str,
    client_id: str | None = None,
    client_secret: str | None = None,
    *,
    download_covers: bool = True,
) -> list[TrackMeta]:
    """
    Возвращает список треков альбома / одного трека / плейлиста Spotify.

    Если client_id/secret не переданы — читаются из .env через config.py.
    Бросает SourceFetchError при ошибке.
    """
    if not client_id or not client_secret:
        try:
            from config import get_spotify_credentials
            client_id, client_secret = get_spotify_credentials()
        except ImportError:
            raise ConfigError(
                "Не переданы client_id/client_secret и не найден config.py."
            )
        except Exception as e:
            raise ConfigError(str(e)) from e

    sp = spotipy.Spotify(auth_manager=SpotifyClientCredentials(
        client_id=client_id, client_secret=client_secret
    ))

    m = re.search(r"(album|track|playlist)[/:]([A-Za-z0-9]+)", url_or_id)
    kind, sid = (m.group(1), m.group(2)) if m else ("album", url_or_id.strip())

    tracks: list[TrackMeta] = []

    try:
        if kind == "album":
            album = sp.album(sid)
            album_name = album["name"]
            album_artist = ", ".join(a["name"] for a in album["artists"])
            year = (album.get("release_date") or "")[:4]
            cover_url = (album.get("images") or [{}])[0].get("url")
            total = album["total_tracks"]
            for i, t in enumerate(sp.album_tracks(sid)["items"], 1):
                tracks.append(TrackMeta(
                    title=t["name"],
                    artist=", ".join(a["name"] for a in t["artists"]),
                    album=album_name,
                    album_artist=album_artist,
                    year=year,
                    track_number=t.get("track_number", i),
                    total_tracks=total,
                    source_id=t["id"],
                    duration=(t.get("duration_ms") or 0) / 1000
                             if t.get("duration_ms") else None,
                ))
            cover_for_all = cover_url

        elif kind == "track":
            t = sp.track(sid)
            album = t.get("album", {})
            tracks.append(TrackMeta(
                title=t["name"],
                artist=", ".join(a["name"] for a in t["artists"]),
                album=album.get("name"),
                album_artist=", ".join(a["name"] for a in album.get("artists", [])),
                year=(album.get("release_date") or "")[:4],
                track_number=t.get("track_number"),
                total_tracks=album.get("total_tracks"),
                source_id=t["id"],
                duration=(t.get("duration_ms") or 0) / 1000
                         if t.get("duration_ms") else None,
            ))
            cover_for_all = (album.get("images") or [{}])[0].get("url")

        elif kind == "playlist":
            pl = sp.playlist(sid)
            for i, item in enumerate(pl["tracks"]["items"], 1):
                t = item.get("track")
                if not t:
                    continue
                album = t.get("album", {})
                tracks.append(TrackMeta(
                    title=t["name"],
                    artist=", ".join(a["name"] for a in t["artists"]),
                    album=album.get("name"),
                    album_artist=", ".join(a["name"] for a in album.get("artists", [])),
                    year=(album.get("release_date") or "")[:4],
                    track_number=t.get("track_number", i),
                    source_id=t["id"],
                    duration=(t.get("duration_ms") or 0) / 1000
                             if t.get("duration_ms") else None,
                ))
            cover_for_all = None  # у каждого трека своя (бля а зач)

        else:
            raise SourceFetchError(f"Неизвестный тип ссылки: {kind}")

    except spotipy.SpotifyException as e:
        raise SourceFetchError(f"Ошибка Spotify API: {e}") from e

    # Скачиваем обложки
    if download_covers:
        if cover_for_all:
            img = _download_image(cover_for_all)
            if img:
                data, mime = img
                for t in tracks:
                    t.cover_data, t.cover_mime = data, mime
        elif kind == "playlist":
            for t in tracks:
                if not t.source_id:
                    continue
                try:
                    tr = sp.track(t.source_id)
                    url = (tr.get("album", {}).get("images") or [{}])[0].get("url")
                    img = _download_image(url)
                    if img:
                        t.cover_data, t.cover_mime = img
                except Exception:
                    pass

    return tracks

def fetch_youtube(
    url_or_id: str,
    *,
    download_covers: bool = True,
) -> list[TrackMeta]:
    """
    Возвращает список треков из YouTube-видео или плейлиста.
    API-ключ не нужен (использует yt-dlp).
    """
    if not url_or_id.startswith("http"):
        url_or_id = f"https://www.youtube.com/watch?v={url_or_id}"

    ydl_opts = {
        "quiet": True,
        "no_warnings": True,
        "extract_flat": False,
        "skip_download": True,
    }

    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url_or_id, download=False)
    except Exception as e:
        raise SourceFetchError(f"Ошибка yt-dlp: {e}") from e

    entries = [e for e in info["entries"] if e] if "entries" in info else [info]
    total = len(entries)
    playlist_title = info.get("title") if "entries" in info else None
    playlist_uploader = info.get("uploader") if "entries" in info else None

    tracks: list[TrackMeta] = []
    cover_urls: list[str | None] = []

    for i, e in enumerate(entries, 1):
        raw_title = e.get("title") or ""
        artist = e.get("artist") or e.get("uploader") or ""
        title = raw_title

        m = re.match(r"^(.+?)\s*[-–—]\s*(.+)$", raw_title)
        if m:
            artist = m.group(1).strip()
            title = m.group(2).strip()

        year = str(e["upload_date"])[:4] if e.get("upload_date") else None

        tracks.append(TrackMeta(
            title=title,
            artist=artist,
            album=playlist_title,
            album_artist=playlist_uploader or artist,
            year=year,
            track_number=i,
            total_tracks=total,
            source_id=e.get("id"),
            duration=float(e["duration"]) if e.get("duration") else None,
        ))
        cover_urls.append(e.get("thumbnail"))

    if download_covers:
        for t, url in zip(tracks, cover_urls):
            img = _download_image(url)
            if img:
                t.cover_data, t.cover_mime = img

    return tracks


def write_tags(path: Path, meta: TrackMeta) -> None:
    """
    Записывает метаданные в аудиофайл. Формат определяется по расширению.
    Бросает UnsupportedFormatError для неподдерживаемых форматов.
    """
    ext = path.suffix.lower()

    if ext == ".mp3":
        try:
            tags = ID3(path)
        except ID3NoHeaderError:
            tags = ID3()
        if meta.title:        tags.add(TIT2(encoding=3, text=meta.title))
        if meta.artist:       tags.add(TPE1(encoding=3, text=meta.artist))
        if meta.album:        tags.add(TALB(encoding=3, text=meta.album))
        if meta.album_artist: tags.add(TPE2(encoding=3, text=meta.album_artist))
        if meta.year:         tags.add(TDRC(encoding=3, text=meta.year))
        if meta.track_number:
            num = str(meta.track_number)
            if meta.total_tracks:
                num += f"/{meta.total_tracks}"
            tags.add(TRCK(encoding=3, text=num))
        if meta.cover_data and meta.cover_mime:
            tags.delall("APIC")
            tags.add(APIC(encoding=3, mime=meta.cover_mime, type=3,
                          desc="Cover", data=meta.cover_data))
        tags.save(path)

    elif ext == ".flac":
        audio = FLAC(path)
        if meta.title:        audio["title"] = meta.title
        if meta.artist:       audio["artist"] = meta.artist
        if meta.album:        audio["album"] = meta.album
        if meta.album_artist: audio["albumartist"] = meta.album_artist
        if meta.year:         audio["date"] = meta.year
        if meta.track_number: audio["tracknumber"] = str(meta.track_number)
        if meta.cover_data and meta.cover_mime:
            audio.clear_pictures()
            pic = Picture()
            pic.type = 3
            pic.mime = meta.cover_mime
            pic.desc = "Cover"
            pic.data = meta.cover_data
            audio.add_picture(pic)
        audio.save()

    elif ext in {".m4a", ".mp4"}:
        audio = MP4(path)
        if meta.title:        audio["\xa9nam"] = [meta.title]
        if meta.artist:       audio["\xa9ART"] = [meta.artist]
        if meta.album:        audio["\xa9alb"] = [meta.album]
        if meta.album_artist: audio["aART"] = [meta.album_artist]
        if meta.year:         audio["\xa9day"] = [meta.year]
        if meta.track_number:
            audio["trkn"] = [(meta.track_number, meta.total_tracks or 0)]
        if meta.cover_data and meta.cover_mime:
            fmt = (MP4Cover.FORMAT_PNG if meta.cover_mime == "image/png"
                   else MP4Cover.FORMAT_JPEG)
            audio["covr"] = [MP4Cover(meta.cover_data, imageformat=fmt)]
        audio.save()

    elif ext in {".ogg", ".opus"}:
        audio = OggVorbis(path) if ext == ".ogg" else OggOpus(path)
        if meta.title:        audio["title"] = meta.title
        if meta.artist:       audio["artist"] = meta.artist
        if meta.album:        audio["album"] = meta.album
        if meta.album_artist: audio["albumartist"] = meta.album_artist
        if meta.year:         audio["date"] = meta.year
        if meta.track_number: audio["tracknumber"] = str(meta.track_number)
        if meta.cover_data and meta.cover_mime:
            pic = Picture()
            pic.type = 3
            pic.mime = meta.cover_mime
            pic.desc = "Cover"
            pic.data = meta.cover_data
            audio["metadata_block_picture"] = [
                base64.b64encode(pic.write()).decode("ascii")
            ]
        audio.save()

    elif ext == ".wav":
        audio = WAVE(path)
        if audio.tags is None:
            audio.add_tags()
        if meta.title:        audio.tags.add(TIT2(encoding=3, text=meta.title))
        if meta.artist:       audio.tags.add(TPE1(encoding=3, text=meta.artist))
        if meta.album:        audio.tags.add(TALB(encoding=3, text=meta.album))
        if meta.year:         audio.tags.add(TDRC(encoding=3, text=meta.year))
        if meta.track_number: audio.tags.add(TRCK(encoding=3, text=str(meta.track_number)))
        if meta.cover_data and meta.cover_mime:
            audio.tags.delall("APIC")
            audio.tags.add(APIC(encoding=3, mime=meta.cover_mime, type=3,
                                desc="Cover", data=meta.cover_data))
        audio.save()

    else:
        raise UnsupportedFormatError(f"Неподдерживаемый формат: {ext}")


def match_file_to_track(
    path: Path,
    tracks: list[TrackMeta],
    *,
    duration_tolerance: float = 0.0,
) -> TrackMeta | None:
    """
    Сопоставляет файл с треком.

    :param duration_tolerance: если > 0 — сначала фильтруем треки по длительности
        файла (±допуск в секундах), затем ищем по имени. Если после фильтра
        остался ровно один трек — берём его.
    """
    stem = normalize(path.stem)

    if duration_tolerance > 0:
        file_dur = get_audio_duration(path)
        if file_dur is not None:
            candidates = [
                t for t in tracks
                if t.duration is not None
                and abs(t.duration - file_dur) <= duration_tolerance
            ]
        else:
            candidates = list(tracks)
    else:
        candidates = list(tracks)

    if candidates and stem:
        for t in candidates:
            if t.normalized_title() == stem:
                return t
        for t in candidates:
            nt = t.normalized_title()
            if nt and (nt in stem or stem in nt):
                return t

    if duration_tolerance > 0 and len(candidates) == 1:
        return candidates[0]

    return None

def process_folder(
    input_dir: Path | str,
    output_dir: Path | str,
    tracks: list[TrackMeta],
    *,
    recursive: bool = False,
    matcher: Callable[[Path, list[TrackMeta]], TrackMeta | None] | None = None,
    on_progress: ProgressCallback | None = None,
    cancel_flag: CancelFlag | None = None,
    copy_files: bool = True,
    duration_tolerance: float = 0.0,
) -> ProcessResult:
    """
    Копирует аудиофайлы из input_dir в output_dir и пишет в них метаданные.

    :param input_dir:    папка с оригиналами
    :param output_dir:   куда копировать
    :param tracks:       список TrackMeta (из fetch_spotify/fetch_youtube)
    :param recursive:    заходить во вложенные папки
    :param matcher:      функция сопоставления (по умолчанию match_file_to_track)
    :param on_progress:  колбэк(i, total, path)
    :param cancel_flag:  колбэк без аргументов; True → остановиться
    :param copy_files:   если False — файлы НЕ копируются, теги пишутся прямо
                         в исходные (оригиналы изменятся)
    :param duration_tolerance: допуск по длительности в секундах (0 = выключено)
    :return:             ProcessResult

    """
    input_dir = Path(input_dir).expanduser()
    output_dir = Path(output_dir).expanduser()

    if not input_dir.is_dir():
        raise MetadataError(f"Не папка: {input_dir}")

    output_dir.mkdir(parents=True, exist_ok=True)

    if matcher is None:
        def _matcher(p: Path, ts: list[TrackMeta]) -> TrackMeta | None:
            return match_file_to_track(p, ts, duration_tolerance=duration_tolerance)
    else:
        _matcher = matcher

    pattern = "**/*" if recursive else "*"
    files = sorted(
        p for p in input_dir.glob(pattern)
        if p.is_file() and p.suffix.lower() in AUDIO_EXTS
    )

    result = ProcessResult(total=len(files))

    for i, src in enumerate(files, 1):
        if cancel_flag and cancel_flag():
            result.cancelled = True
            break

        rel = src.relative_to(input_dir)
        dst = output_dir / rel
        dst.parent.mkdir(parents=True, exist_ok=True)

        try:
            if copy_files:
                shutil.copy2(src, dst)
            else:
                dst = src  # пишем прямо в оригинал

            meta = _matcher(src, tracks)
            if meta:
                write_tags(dst, meta)
                result.files.append(FileResult(src, dst, ok=True, matched=meta.title))
                result.ok += 1
            else:
                result.files.append(FileResult(src, dst, ok=True, matched=None))
                result.skipped += 1

        except Exception as e:
            result.files.append(FileResult(src, dst, ok=False, error=str(e)))
            result.failed += 1

        if on_progress:
            on_progress(i, len(files), src)

    return result


__all__ = [
    # исключения
    "MetadataError", "SourceFetchError", "ConfigError", "UnsupportedFormatError",
    # данные
    "TrackMeta", "FileResult", "ProcessResult",
    # функции
    "fetch_spotify", "fetch_youtube", "write_tags",
    "process_folder", "match_file_to_track", "normalize",
    "get_audio_duration",

    "AUDIO_EXTS",
    
    "ProgressCallback", "CancelFlag",
]