import shutil
import subprocess

def is_ffmpeg_installed() -> bool:
    """Kiểm tra xem ffmpeg đã có trong PATH hệ thống hay chưa"""
    return shutil.which("ffmpeg") is not None

def get_ffmpeg_version() -> str:
    """Lấy thông tin phiên bản ffmpeg nếu có"""
    if not is_ffmpeg_installed():
        return "Not installed"
    try:
        res = subprocess.run(["ffmpeg", "-version"], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        return res.stdout.split("\n")[0]
    except Exception as e:
        return f"Error: {e}"
