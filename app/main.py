import os, sys, time, socket, sqlite3, threading, requests
import subprocess
from datetime import datetime, timezone
from pyais import decode
from target_manager import find_candidates
from outreach import (TELEGRAM_API_URL, notify_candidates, send_manual, tg)

if os.getenv("MOCK_SDR", "false").lower() == "true":
    from tools.sdr_mock import run_multiplexer
else:
    from sdr_multiplexer import run_multiplexer

AIS_PORT = int(os.getenv("AIS_LOCAL_PORT", 10110))
CAL_OFF  = float(os.getenv("RDF_CAL_OFFSET", 0.0))
INVERT   = os.getenv("RDF_INVERT_SIGN", "false").lower() == "true"
TG_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
TG_CHAT  = os.getenv("TELEGRAM_CHAT_ID", "")
TG_OPERATOR_ID = os.getenv("TELEGRAM_OPERATOR_ID", "")
DB_PATH  = os.getenv("DB_PATH", "/tmp/shipyard.db")
CLOCK_MIN_TIMESTAMP = datetime(2024, 1, 1, tzinfo=timezone.utc).timestamp()
CLOCK_RETRY_SECONDS = 5

if not TG_OPERATOR_ID:
    print("[WARN] TELEGRAM_OPERATOR_ID is unset; callback user authorization is disabled",
          file=sys.stderr)

os.makedirs(os.path.dirname(DB_PATH) or "/tmp", exist_ok=True)
conn = sqlite3.connect(DB_PATH, check_same_thread=False, timeout=10.0)
conn.execute("PRAGMA journal_mode=WAL")
conn.execute("PRAGMA synchronous=NORMAL")
conn.execute("PRAGMA busy_timeout=10000")
conn.execute("PRAGMA foreign_keys=ON")
with conn:
    conn.execute("""CREATE TABLE IF NOT EXISTS vessels (
        mmsi INTEGER PRIMARY KEY, imo INTEGER, shipname TEXT, length INTEGER,
        lat REAL, lon REAL, last_seen REAL,
        prev_lat REAL, prev_lon REAL, prev_seen REAL,
        cog REAL, sog REAL, proposal_sent INTEGER DEFAULT 0,
        proposal_pending_at REAL DEFAULT NULL)""")
    columns = {row[1] for row in conn.execute("PRAGMA table_info(vessels)")}
    if "proposal_pending_at" not in columns:
        conn.execute(
            "ALTER TABLE vessels ADD COLUMN proposal_pending_at REAL DEFAULT NULL")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_vessels_last_seen "
                 "ON vessels(last_seen)")

lock = threading.Lock()
POSITION_TYPES = {1, 2, 3, 18, 19}
STATIC_TYPES   = {5, 24}

def _recover_stale_pending():
    with lock:
        with conn:
            conn.execute("""UPDATE vessels SET
                proposal_sent=0, proposal_pending_at=NULL
                WHERE proposal_sent=1 AND proposal_pending_at IS NOT NULL
                  AND (? - proposal_pending_at) > 600""", (time.time(),))

_recover_stale_pending()

def on_bearing(bearing, quality):
    t = time.time()
    with lock:
        cand = find_candidates(conn, bearing, bearing_time=t, now=t)
    threading.Thread(target=notify_candidates, args=(bearing, quality, cand),
                     daemon=True).start()

def _upsert_position(mmsi, lat, lon, cog, sog, now):
    conn.execute("""
        INSERT INTO vessels (mmsi, lat, lon, last_seen, cog, sog)
        VALUES (?, ?, ?, ?, ?, ?)
        ON CONFLICT(mmsi) DO UPDATE SET
            prev_lat  = vessels.lat,
            prev_lon  = vessels.lon,
            prev_seen = vessels.last_seen,
            lat       = excluded.lat,
            lon       = excluded.lon,
            last_seen = excluded.last_seen,
            cog       = excluded.cog,
            sog       = excluded.sog
    """, (mmsi, lat, lon, now, cog, sog))

def _upsert_static(mmsi, imo, name, length, now, has_position=False):
    if has_position:
        conn.execute("""
            UPDATE vessels SET
                imo      = COALESCE(NULLIF(?, 0), imo),
                shipname = COALESCE(NULLIF(?, ''), shipname),
                length   = COALESCE(NULLIF(?, 0), length)
            WHERE mmsi = ?
        """, (imo, name, length, mmsi))
    else:
        conn.execute("""
            INSERT INTO vessels (mmsi, imo, shipname, length, last_seen)
            VALUES (?, ?, ?, ?, 0)
            ON CONFLICT(mmsi) DO UPDATE SET
                imo      = COALESCE(NULLIF(excluded.imo, 0),      vessels.imo),
                shipname = COALESCE(NULLIF(excluded.shipname,''), vessels.shipname),
                length   = COALESCE(NULLIF(excluded.length, 0),   vessels.length)
        """, (mmsi, imo, name, length))

def listen_ais():
    from pyais import IterMessages
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 1 << 20)
    s.bind(("127.0.0.1", AIS_PORT))
    print("[AIS] UDP :%d" % AIS_PORT, file=sys.stderr)

    def udp_lines():
        while True:
            try:
                data, _ = s.recvfrom(4096)
            except Exception as e:
                print("[AIS] recv: %s" % e, file=sys.stderr); continue
            for line in data.splitlines():
                line = line.strip()
                if line:
                    yield line

    dbg_count = [0]

    def _extract_dict(msg):
        # pyais 2.6.2: IterMessages yields NMEAMessage; .decode() -> AISSentence
        try:
            decoded = msg.decode() if hasattr(msg, "decode") else msg
        except Exception as e:
            if dbg_count[0] < 3:
                print("[AIS] decode err: %s" % e, file=sys.stderr)
                dbg_count[0] += 1
            return None
        try:
            d = decoded.asdict()
        except Exception as e:
            if dbg_count[0] < 3:
                print("[AIS] asdict err: %s" % e, file=sys.stderr)
                dbg_count[0] += 1
            return None
        if isinstance(d, dict):
            return d if d else None
        if isinstance(d, list) and d and isinstance(d[0], dict):
            return d[0]
        return None

    try:
        for msg in IterMessages(udp_lines()):
            m = _extract_dict(msg)
            if not m:
                continue
            mmsi = m.get("mmsi")
            if not mmsi:
                continue
            mt  = m.get("msg_type")
            now = time.time()
            try:
                with lock:
                    if mt in POSITION_TYPES:
                        lat, lon = m.get("lat"), m.get("lon")
                        if (lat is None or lon is None
                                or abs(lat) > 90 or abs(lon) > 180):
                            continue
                        _upsert_position(mmsi, lat, lon,
                                         m.get("course"), m.get("speed"), now)
                        if mt == 19:
                            _upsert_static(
                                mmsi,
                                int(m.get("imo") or 0),
                                (m.get("shipname") or "").strip(),
                                (m.get("to_bow") or 0) + (m.get("to_stern") or 0),
                                now, has_position=True)
                    elif mt in STATIC_TYPES:
                        imo = int(m.get("imo") or 0) if mt == 5 else 0
                        name = (m.get("shipname") or "").strip()
                        length = (m.get("to_bow") or 0) + (m.get("to_stern") or 0)
                        print("[AIS] type=%s mmsi=%s name=%r imo=%s len=%s"
                              % (mt, mmsi, name, imo, length),
                              file=sys.stderr)
                        if not (imo or name or length):
                            continue
                        _upsert_static(mmsi, imo, name, length, now,
                                       has_position=False)
                    conn.commit()
            except sqlite3.OperationalError as e:
                print("[AIS] SQLite: %s" % e, file=sys.stderr); continue
            except Exception as e:
                print("[AIS] handler: %s" % e, file=sys.stderr); continue
    except Exception as e:
        print("[AIS] stream error: %s" % e, file=sys.stderr)


def wait_for_clock():
    while True:
        try:
            result = subprocess.run(
                ["timedatectl", "show", "--property=NTPSynchronized", "--value"],
                check=True, capture_output=True, text=True, timeout=5)
        except (OSError, subprocess.SubprocessError) as e:
            if time.time() >= CLOCK_MIN_TIMESTAMP:
                print("[CLOCK] cannot verify NTP synchronization (%s); "
                      "system time is plausible, continuing" % e,
                      file=sys.stderr)
                return
            print("[CLOCK] waiting for a valid system time; NTP check failed: %s"
                  % e, file=sys.stderr)
        else:
            if result.stdout.strip().lower() == "yes":
                print("[CLOCK] NTP synchronized", file=sys.stderr)
                return
            print("[CLOCK] waiting for NTP synchronization", file=sys.stderr)
        time.sleep(CLOCK_RETRY_SECONDS)



def _authorized_callback(cb):
    try:
        msg_chat = (cb.get("message") or {}).get("chat") or {}
        if str(msg_chat.get("id", "")) != str(TG_CHAT):
            return False
        if TG_OPERATOR_ID:
            from_user = cb.get("from") or {}
            if str(from_user.get("id", "")) != str(TG_OPERATOR_ID):
                return False
        return True
    except Exception:
        return False

def tg_poll_loop():
    if not TG_TOKEN:
        print("[TGPOLL] disabled", file=sys.stderr); return
    offset, backoff = 0, 1
    while True:
        try:
            r = requests.get(
                "%s/bot%s/getUpdates" % (TELEGRAM_API_URL, TG_TOKEN),
                params={"offset": offset, "timeout": 30}, timeout=35).json()
            backoff = 1
            for u in r.get("result", []):
                offset = u["update_id"] + 1
                cb = u.get("callback_query")
                if not cb or not _authorized_callback(cb):
                    continue
                d = cb.get("data", "")
                if not d.startswith("send:"):
                    continue
                try:
                    _, mmsi_s, bearing_s, dist_s = d.split(":", 3)
                    mmsi, bearing, dist = int(mmsi_s), float(bearing_s), float(dist_s)
                except (ValueError, IndexError):
                    continue
                ok = send_manual(conn, lock, mmsi, bearing, dist)
                requests.post(
                    "%s/bot%s/answerCallbackQuery"
                    % (TELEGRAM_API_URL, TG_TOKEN),
                    json={"callback_query_id": cb["id"],
                          "text": "Sent" if ok else "Error"},
                    timeout=10)
        except requests.RequestException as e:
            print("[TGPOLL] net: %s" % e, file=sys.stderr)
            time.sleep(backoff); backoff = min(backoff * 2, 60)
        except Exception as e:
            print("[TGPOLL] unexpected: %s" % e, file=sys.stderr); time.sleep(5)

def main():
    wait_for_clock()
    _recover_stale_pending()
    threading.Thread(target=listen_ais,   daemon=True, name="ais").start()
    threading.Thread(target=tg_poll_loop, daemon=True, name="tg").start()
    print("[MAIN] run_multiplexer()...", file=sys.stderr)
    run_multiplexer(cal_offset_deg=CAL_OFF, invert_sign=INVERT,
                    on_bearing_callback=on_bearing)

if __name__ == "__main__":
    main()
