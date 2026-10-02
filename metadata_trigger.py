#!/usr/bin/env python3
"""
Записывает метаданные из Spotify/YouTube в аудиофайлы.
"""
import base64
import re
import shutil
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path

import requests
import spotipy
from spotipy.oauth2 import SpotifyClientCredentials
import yt_dlp

from mutagen import File as MutagenFile
from mutagen.id3 import (
    ID3, TIT2, TPE1, TALB, TDRC, TRCK, TPE2, APIC, ID3NoHeaderError
)
from mutagen.flac import FLAC, Picture
from mutagen.mp4 import MP4, MP4Cover
from mutagen.oggvorbis import OggVorbis
from mutagen.oggopus import OggOpus
from mutagen.wave import WAVE


AUDIO_EXTS = {".mp3", ".flac", ".m4a", ".mp4", ".ogg", ".opus", ".wav"}
IMAGE_EXTS = {".jpg", ".jpeg", ".png"}


@dataclass
class TrackMeta:
    title: str | None = None
    artist: str | None = None
    album: str | None = None
    album_artist: str | None = None
    year: str | None = None
    track_number: int | None = None
    total_tracks: int | None = None
    cover_url: str | None = None
    cover_data: bytes | None = None
    mime: str | None = None
    source_id: str | None = None
    duration: float | None = None

@dataclass
class FileResult:
    source: Path
    target: Path
    ok: bool
    matched: str | None = None
    error: str | None = None

@dataclass
class ProcessResult:
    total: int = 0
    ok: int = 0
    failed: int = 0
    skipped: int = 0
    files: list[FileResult] = field(default_factory=list)


def normalize(s: str) -> str:
    """Нормализует строку для нечёткого сравнения названий."""
    s = unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode()
    s = s.lower()
    s = re.sub(r"[\(\[].*?[\)\]]", "", s)
    s = re.sub(r"[^a-z0-9]+", " ", s)
    return re.sub(r"\s+", " ", s).strip()

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

def ask(prompt: str) -> str:
    while True:
        v = input(prompt).strip().strip('"').strip("'")
        if v:
            return v
        print("  ! Пустое значение, попробуйте снова.")

def ask_yes_no(prompt: str, default: bool = False) -> bool:
    suffix = "[Y/n]" if default else "[y/N]"
    while True:
        a = input(f"{prompt} {suffix}: ").strip().lower()
        if not a:
            return default
        if a in {"y", "yes", "д", "да"}:
            return True
        if a in {"n", "no", "н", "нет"}:
            return False

def download_image(url: str) -> tuple[bytes, str] | None:
    """Скачивает картинку, возвращает (data, mime) или None."""
    if not url:
        return None
    try:
        r = requests.get(url, timeout=15)
        r.raise_for_status()
        mime = r.headers.get("Content-Type", "image/jpeg").split(";")[0].strip()
        if mime not in {"image/jpeg", "image/png"}:
            mime = "image/jpeg"
        return r.content, mime
    except Exception as e:
        print(f"  ! Не удалось скачать обложку: {e}")
        return None

# ХУЯТИНА не работает нужен прем
def fetch_spotify(url_or_id: str, client_id: str, client_secret: str) -> list[TrackMeta]:
    """
    Возвращает список треков альбома/трека из Spotify.
    Поддерживает: album URL, track URL, playlist URL, просто ID.
    """
    sp = spotipy.Spotify(auth_manager=SpotifyClientCredentials(
        client_id=client_id, client_secret=client_secret
    ))

    # https://open.spotify.com/album/XXXX
    # https://open.spotify.com/track/XXXX
    # https://open.spotify.com/playlist/XXXX
    m = re.search(r"(album|track|playlist)[/:]([A-Za-z0-9]+)", url_or_id)
    if m:
        kind, sid = m.group(1), m.group(2)
    else:
        kind, sid = "album", url_or_id.strip()

    tracks: list[TrackMeta] = []

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
                cover_url=cover_url,
                source_id=t["id"],
                duration=(t.get("duration_ms") or 0) / 1000
                         if t.get("duration_ms") else None,
            ))

    elif kind == "track":
        t = sp.track(sid)
        album = t.get("album", {})
        cover_url = (album.get("images") or [{}])[0].get("url")
        tracks.append(TrackMeta(
            title=t["name"],
            artist=", ".join(a["name"] for a in t["artists"]),
            album=album.get("name"),
            album_artist=", ".join(a["name"] for a in album.get("artists", [])),
            year=(album.get("release_date") or "")[:4],
            track_number=t.get("track_number"),
            total_tracks=album.get("total_tracks"),
            cover_url=cover_url,
            source_id=t["id"],
            duration=(t.get("duration_ms") or 0) / 1000
                     if t.get("duration_ms") else None,
        ))

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
                cover_url=(album.get("images") or [{}])[0].get("url"),
                source_id=t["id"],
                duration=(t.get("duration_ms") or 0) / 1000
                         if t.get("duration_ms") else None,
            ))

    else:
        raise ValueError(f"Неизвестный тип ссылки: {kind}")

    if tracks and tracks[0].cover_url:
        img = download_image(tracks[0].cover_url)
        if img:
            data, mime = img
            for t in tracks:
                t.cover_data = data
                t.mime = mime

    return tracks

# !
def fetch_youtube(url_or_id: str) -> list[TrackMeta]:
    """
    Извлекает метаданные из YouTube-видео или плейлиста.
    Использует yt-dlp — API-ключ не нужен.
    """
    if not url_or_id.startswith("http"):
        url_or_id = f"https://www.youtube.com/watch?v={url_or_id}"

    ydl_opts = {
        "quiet": True,
        "no_warnings": True,
        "extract_flat": False,
        "skip_download": True,
    }

    tracks: list[TrackMeta] = []

    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        info = ydl.extract_info(url_or_id, download=False)

    if "entries" in info:
        entries = [e for e in info["entries"] if e]
    else:
        entries = [info]

    total = len(entries)

    for i, e in enumerate(entries, 1):
        raw_title = e.get("title") or ""
        artist = e.get("artist") or e.get("uploader") or ""
        title = raw_title

        m = re.match(r"^(.+?)\s*[-–—]\s*(.+)$", raw_title)
        if m:
            artist = m.group(1).strip()
            title = m.group(2).strip()

        year = None
        if e.get("upload_date"):
            year = str(e["upload_date"])[:4]

        album = info.get("title") if "entries" in info else None
        album_artist = info.get("uploader") if "entries" in info else artist

        tracks.append(TrackMeta(
            title=title,
            artist=artist,
            album=album,
            album_artist=album_artist,
            year=year,
            track_number=i,
            total_tracks=total,
            cover_url=e.get("thumbnail"),
            source_id=e.get("id"),
            duration=float(e["duration"]) if e.get("duration") else None,
        ))

    for t in tracks:
        if t.cover_url:
            img = download_image(t.cover_url)
            if img:
                t.cover_data, t.mime = img

    return tracks


def write_tags(path: Path, meta: TrackMeta) -> None:
    """Записывает метаданные в аудиофайл (в зависимости от формата)."""
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
        if meta.cover_data and meta.mime:
            tags.delall("APIC")
            tags.add(APIC(encoding=3, mime=meta.mime, type=3,
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
        if meta.cover_data and meta.mime:
            audio.clear_pictures()
            pic = Picture()
            pic.type = 3
            pic.mime = meta.mime
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
        if meta.cover_data and meta.mime:
            fmt = MP4Cover.FORMAT_PNG if meta.mime == "image/png" else MP4Cover.FORMAT_JPEG
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
        if meta.cover_data and meta.mime:
            pic = Picture()
            pic.type = 3
            pic.mime = meta.mime
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
        if meta.cover_data and meta.mime:
            audio.tags.delall("APIC")
            audio.tags.add(APIC(encoding=3, mime=meta.mime, type=3,
                                desc="Cover", data=meta.cover_data))
        audio.save()


def process_folder(
    input_dir: Path,
    output_dir: Path,
    tracks: list[TrackMeta],
    recursive: bool = False,
    duration_tolerance: float = 0.0,
) -> ProcessResult:
    """Копирует файлы, сопоставляет с треками и записывает теги."""
    output_dir.mkdir(parents=True, exist_ok=True)

    pattern = "**/*" if recursive else "*"
    files = sorted(
        p for p in input_dir.glob(pattern)
        if p.is_file() and p.suffix.lower() in AUDIO_EXTS
    )

    result = ProcessResult(total=len(files))

    for src in files:
        rel = src.relative_to(input_dir)
        dst = output_dir / rel
        dst.parent.mkdir(parents=True, exist_ok=True)

        if duration_tolerance > 0:
            file_dur = get_audio_duration(src)
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

        meta: TrackMeta | None = None
        stem = normalize(src.stem)
        if candidates and stem:
            for t in candidates:
                if normalize(t.title or "") == stem:
                    meta = t
                    break
            if not meta:
                for t in candidates:
                    key = normalize(t.title or "")
                    if key and (key in stem or stem in key):
                        meta = t
                        break

        if not meta and duration_tolerance > 0 and len(candidates) == 1:
            meta = candidates[0]

        try:
            shutil.copy2(src, dst)
            if meta:
                write_tags(dst, meta)
                result.files.append(FileResult(src, dst, ok=True,
                                               matched=meta.title))
                result.ok += 1
            else:
                result.files.append(FileResult(src, dst, ok=True,
                                               matched=None))
                result.skipped += 1
        except Exception as e:
            result.files.append(FileResult(src, dst, ok=False, error=str(e)))
            result.failed += 1

    return result


def main():
    print("=" * 60)
    print("  Запись метаданных из Spotify/YouTube в аудиофайлы")
    print("=" * 60)

    while True:
        src = input("Источник (1 - Spotify, 2 - YouTube): ").strip()
        if src in {"1", "2"}:
            break
        print("  ! Введите 1 или 2.")

    if src == "1":
        from config import get_spotify_credentials, ConfigError
        try:
            client_id, client_secret = get_spotify_credentials()
        except ConfigError as e:
            print(f"\n{e}")
            return
        url = ask("Ссылка на альбом/трек/плейлист Spotify: ")
        print("\nЗагружаю метаданные из Spotify...")
        tracks = fetch_spotify(url, client_id, client_secret)
    else:
        url = ask("Ссылка на видео/плейлист YouTube: ")
        print("\nЗагружаю метаданные из YouTube...")
        tracks = fetch_youtube(url)

    if not tracks:
        print("Не удалось получить метаданные.")
        return

    print(f"Найдено треков: {len(tracks)}")
    for t in tracks[:5]:
        print(f"  • {t.artist or '?'} — {t.title or '?'}")
    if len(tracks) > 5:
        print(f"  ... и ещё {len(tracks) - 5}")

    while True:
        raw = ask("Папка с музыкой: ")
        input_dir = Path(raw).expanduser()
        if input_dir.is_dir():
            break
        print(f"  ! Не папка: {input_dir}")

    while True:
        raw = ask("Куда сохранить результат: ")
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

    use_duration = ask_yes_no(
        "Сопоставлять ещё и по длительности трека?", default=False
    )
    duration_tolerance = 0.0
    if use_duration:
        while True:
            raw = ask("Допуск по длительности, сек (например 3): ")
            try:
                duration_tolerance = float(raw.replace(",", "."))
                if duration_tolerance > 0:
                    break
            except ValueError:
                pass
            print("  ! Введите положительное число.")

    print("\n--- Параметры ---")
    print(f"  Источник : {'Spotify' if src == '1' else 'YouTube'}")
    print(f"  Треков   : {len(tracks)}")
    print(f"  Музыка   : {input_dir.resolve()}")
    print(f"  Результат: {output_dir.resolve()}")
    print(f"  Рекурсия : {'да' if recursive else 'нет'}")
    if use_duration:
        print(f"  Длит-ть  : ±{duration_tolerance:g} сек")
    else:
        print(f"  Длит-ть  : выключено")
    if not ask_yes_no("Запускаем?", default=True):
        print("Отменено.")
        return

    print()
    result = process_folder(
        input_dir, output_dir, tracks,
        recursive=recursive,
        duration_tolerance=duration_tolerance,
    )

    print("\n" + "=" * 60)
    print(f"Готово. Тегировано: {result.ok}, "
          f"пропущено (нет совпадений): {result.skipped}, "
          f"ошибок: {result.failed}")
    print(f"Результат в: {output_dir.resolve()}")
    for f in result.files:
        if f.error:
            print(f"  [FAIL] {f.source.name}: {f.error}")
        elif f.matched:
            print(f"  [OK]   {f.source.name}  →  {f.matched}")
        else:
            print(f"  [SKIP] {f.source.name}  (нет совпадения)")

if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n\nПрервано пользователем.")