"""用 yt-dlp API 下载抖音视频（Cookie 从 douyin_cookies.txt 读取）"""
import os

import yt_dlp

URL = "https://v.douyin.com/xkNATZ7Xbos/"
with open("douyin_cookies.txt", encoding="utf-8") as f:
    cookie = f.read().strip()

opts = {
    "outtmpl": os.path.join("downloads", "%(title).80s.%(ext)s"),
    "http_headers": {
        "Cookie": cookie,
        "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                       "(KHTML, like Gecko) Chrome/151.0.0.0 Safari/537.36 Edg/151.0.0.0"),
        "Referer": "https://www.douyin.com/",
    },
    "format": "bestvideo+bestaudio/best",
    "merge_output_format": "mp4",
    "noplaylist": True,
}

with yt_dlp.YoutubeDL(opts) as ydl:
    ydl.download([URL])
print("下载完成，产物在 downloads/ 目录")
