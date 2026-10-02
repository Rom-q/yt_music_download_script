# Music Downloader & Metadata Tool

A set of Python scripts for downloading music from YouTube, fetching metadata from Spotify/YouTube, embedding cover art, and writing ID3 tags to audio files.

## Features

- Download audio or video from YouTube (single videos or entire playlists)
- Choose video quality (up to 4K) and audio quality (128–320 kbps)
- Fetch metadata from Spotify (albums, tracks, playlists) or YouTube
- Automatically match downloaded files with fetched metadata
- Embed cover art into audio files
- Write tags for MP3, FLAC, M4A, MP4, OGG, OPUS, WAV
- Unified interactive menu (`main.py`) and standalone scripts

## Modules

| File | Description |
|------|-------------|
| `main.py` | Unified CLI interface combining all features |
| `yt_downloader.py` | YouTube downloader (video/audio, playlists) |
| `metadata_lib.py` | Library for fetching metadata from Spotify/YouTube and writing tags |
| `cover_lib.py` | Library for embedding cover art into audio files |
| `config.py` | Handles Spotify API credentials from `.env` |
| `add_cover.py` | Standalone script to add covers to a folder of music |
| `metadata_trigger.py` | Standalone script for metadata fetching and tagging |

# DOWNLOAD [RELEASE](https://github.com/Rom-q/yt_music_download_script/releases/tag/1.0.1) .exe

## Requirements

- Python 3.10+
- Packages: `yt-dlp`, `spotipy`, `mutagen`, `requests`, `python-dotenv`
- FFmpeg (bundled in the release executable; otherwise install separately)

Install dependencies:

```bash
pip install yt-dlp spotipy mutagen requests python-dotenv
