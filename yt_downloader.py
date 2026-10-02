import os
import sys
import yt_dlp

DOWNLOAD_DIR = "downloads"


def get_ffmpeg_path():
    """Возвращает путь к ffmpeg.exe, встроенному в программу."""
    if getattr(sys, 'frozen', False):
        base_path = sys._MEIPASS
    else:
        base_path = os.path.dirname(os.path.abspath(__file__))
    
    return os.path.join(base_path, 'ffmpeg_bin')


def ensure_download_dir():
    if not os.path.exists(DOWNLOAD_DIR):
        os.makedirs(DOWNLOAD_DIR)


def progress_hook(d):
    if d['status'] == 'downloading':
        percent = d.get('_percent_str', '').strip()
        speed = d.get('_speed_str', '').strip()
        eta = d.get('_eta_str', '').strip()
        info = d.get('info_dict', {})
        playlist_idx = info.get('playlist_index')
        playlist_count = info.get('n_entries')
        prefix = ""
        if playlist_idx and playlist_count:
            prefix = f"[{playlist_idx}/{playlist_count}] "
        title = info.get('title', '')[:50]
        sys.stdout.write(
            f"\r  {prefix}{title} | {percent} | {speed} | ETA: {eta}     "
        )
        sys.stdout.flush()
    elif d['status'] == 'finished':
        print("\n  ✔ Файл загружен, идёт постобработка...")


def print_menu():
    print("\n" + "=" * 55)
    print("             YouTube Downloader")
    print("=" * 55)
    print("  1. Скачать только видео (без звука)")
    print("  2. Скачать только звук")
    print("  3. Скачать видео вместе со звуком")
    print("  0. Выход")
    print("=" * 55)


def ask_video_quality():
    """Спрашивает качество видео."""
    print("\n  Выберите качество видео:")
    print("    1. Лучшее доступное")
    print("    2. 2160p (4K)")
    print("    3. 1440p (2K)")
    print("    4. 1080p")
    print("    5. 720p")
    print("    6. 480p")
    print("    7. 360p")
    choice = input("  Ваш выбор (по умолчанию 1): ").strip() or "1"

    mapping = {
        "1": "bestvideo",
        "2": "bestvideo[height<=2160]",
        "3": "bestvideo[height<=1440]",
        "4": "bestvideo[height<=1080]",
        "5": "bestvideo[height<=720]",
        "6": "bestvideo[height<=480]",
        "7": "bestvideo[height<=360]",
    }
    return mapping.get(choice, "bestvideo")


def ask_audio_quality():
    """Спрашивает качество звука (для mp3)."""
    print("\n  Выберите качество звука (kbps):")
    print("    1. Лучшее доступное")
    print("    2. 320")
    print("    3. 256")
    print("    4. 192")
    print("    5. 128")
    choice = input("  Ваш выбор (по умолчанию 4): ").strip() or "4"

    mapping = {
        "1": "0",     # 0 = best в FFmpegExtractAudio
        "2": "320",
        "3": "256",
        "4": "192",
        "5": "128",
    }
    return mapping.get(choice, "192")



def collect_urls():
    """Собирает ссылки, пока пользователь не введёт пустую строку."""
    print("\nВведите ссылки на видео или плейлисты.")
    print("Каждую ссылку — с новой строки. Пустая строка = завершить ввод.\n")

    urls = []
    while True:
        line = input(f"  [{len(urls)+1}] > ").strip()
        if not line:
            if not urls:
                print("Вы не ввели ни одной ссылки.")
                continue
            break
        urls.append(line)
    return urls


def preview_urls(urls):
    """Показывает информацию по ссылкам: одиночное видео или плейлист, и сколько элементов."""
    print("\nАнализирую ссылки...\n")
    preview_opts = {
        'quiet': True,
        'no_warnings': True,
        'extract_flat': True,
        'skip_download': True,
    }

    total_items = 0
    for i, url in enumerate(urls, 1):
        try:
            with yt_dlp.YoutubeDL(preview_opts) as ydl:
                info = ydl.extract_info(url, download=False)

            if info.get('_type') == 'playlist':
                count = info.get('playlist_count') or len(info.get('entries') or [])
                title = info.get('title', 'Без названия')
                print(f"  {i}.Плейлист: «{title}» — {count} видео")
                total_items += count
            else:
                title = info.get('title', 'Без названия')
                duration = info.get('duration') or 0
                mins, secs = divmod(int(duration), 60)
                print(f"  {i}.Видео: «{title}» ({mins}:{secs:02d})")
                total_items += 1
        except Exception as e:
            print(f"  {i}.Ошибка при анализе ссылки: {e}")

    print(f"\n  Итого к скачиванию: {total_items} файл(ов).")
    return total_items



def build_ydl_opts(mode, video_fmt, audio_quality, use_playlist=True):
    """
    mode: '1' video only, '2' audio only, '3' video+audio
    video_fmt: строка формата, например 'bestvideo[height<=1080]'
    audio_quality: строка '192', '320' и т.п. для mp3
    """
    opts = {
        'outtmpl': os.path.join(DOWNLOAD_DIR, '%(title)s.%(ext)s'),
        'quiet': True,
        'no_warnings': True,
        'progress_hooks': [progress_hook],
        'ignoreerrors': True,
        'noplaylist': not use_playlist,
        'writethumbnail': False,
        'ffmpeg_location': get_ffmpeg_path(),
    }

    if mode == '1':
        # только видео (без звука)
        opts.update({
            'format': video_fmt,
            'merge_output_format': 'mp4',
        })

    elif mode == '2':
        # только звук
        opts.update({
            'format': 'bestaudio/best',
            'postprocessors': [{
                'key': 'FFmpegExtractAudio',
                'preferredcodec': 'mp3',
                'preferredquality': audio_quality,
            }],
        })

    elif mode == '3':
        # ВИДЕО + ЗВУК С ОБЪЕДИНЕНИЕМ В ОДИН ФАЙЛ
        opts.update({
            'format': f'{video_fmt}+bestaudio/{video_fmt}/best',
            'merge_output_format': 'mp4',
            'postprocessors': [
                {
                    'key': 'FFmpegVideoRemuxer',
                    'preferedformat': 'mp4',
                },
                {
                    'key': 'FFmpegMetadata',
                    'add_metadata': True,
                },
            ],
        })

    return opts



def download_all(urls, mode, video_fmt, audio_quality, use_playlist=True):
    ensure_download_dir()
    opts = build_ydl_opts(mode, video_fmt, audio_quality, use_playlist)

    print("\n" + "=" * 55)
    print("  Начинаю загрузку...")
    print("=" * 55)

    try:
        with yt_dlp.YoutubeDL(opts) as ydl:
            ydl.download(urls)
        print("\n\nВсе загрузки завершены! Файлы в:", os.path.abspath(DOWNLOAD_DIR))
    except yt_dlp.utils.DownloadError as e:
        print(f"\nОшибка загрузки: {e}")
    except KeyboardInterrupt:
        print("\n\nЗагрузка прервана пользователем.")
    except Exception as e:
        print(f"\nНепредвиденная ошибка: {e}")



def main():
    print("Добро пожаловать в YouTube Downloader!")
    print("Поддерживаются видео и плейлисты.")
    print("Для режимов 2 и 3 нужен установленный FFmpeg.")

    while True:
        print_menu()
        mode = input("Выберите режим: ").strip()

        if mode == '0':
            return
        if mode not in ('1', '2', '3'):
            print("Неверный выбор.")
            continue

        video_fmt = "bestvideo"
        audio_quality = "192"

        if mode in ('1', '3'):
            video_fmt = ask_video_quality()
        if mode in ('2', '3'):
            audio_quality = ask_audio_quality()

        use_playlist = True
        if mode in ('1', '2', '3'):
            ans = input("\n  Скачивать плейлисты целиком? (Y/n): ").strip().lower()
            use_playlist = (ans != 'n')

        urls = collect_urls()

        preview_urls(urls)

        confirm = input("\nНачать скачивание? (Y/n): ").strip().lower()
        if confirm and confirm != 'y':
            print("Отменено. Возврат в меню.\n")
            continue

        download_all(urls, mode, video_fmt, audio_quality, use_playlist)



if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n\nПрервано пользователем.")