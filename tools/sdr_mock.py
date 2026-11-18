"""
Mock-заменитель run_multiplexer для тестирования без RTL-SDR.
Активация: MOCK_SDR=true в .env или окружении.

Каждые MOCK_BEARING_INTERVAL_S секунд вызывает on_bearing_callback
с заданным пеленгом. Telegram-бот и БД работают как в проде.
"""
import os, time, sys

def run_multiplexer(cal_offset_deg=0.0, invert_sign=False,
                    on_bearing_callback=None):
    interval = float(os.getenv("MOCK_BEARING_INTERVAL_S", "30"))
    bearing  = float(os.getenv("MOCK_BEARING_DEG", "0.0"))
    quality  = float(os.getenv("MOCK_BEARING_QUALITY", "0.95"))
    count    = int(os.getenv("MOCK_BEARING_COUNT", "0"))   # 0 = бесконечно

    print(f"[MOCK] bearing={bearing}°, q={quality}, "
          f"interval={interval}s, count={count or '∞'}", file=sys.stderr)

    n = 0
    while count == 0 or n < count:
        time.sleep(interval)
        n += 1
        if on_bearing_callback:
            try:
                on_bearing_callback(bearing, quality)
                print(f"[MOCK] bearing callback #{n}", file=sys.stderr)
            except Exception as e:
                print(f"[MOCK] callback error: {e}", file=sys.stderr)