import os, time, socket, subprocess, sys
import numpy as np
from scipy.signal import butter, lfilter, resample_poly
from rtlsdr import RtlSdr

SAMPLE_RATE  = 1024000
FREQ_AIS     = 162_000_000
FREQ_RDF     = 156_400_000
OFFSET_CH16  = +400_000
OFFSET_PILOT = -400_000
ROTATION_HZ  = 250.0
FS_AUDIO     = SAMPLE_RATE // 64
SWITCH_PORT  = 4210
SQUELCH_SNR_DB = 12.0
ENABLE_CH16_SCAN = os.getenv("ENABLE_CH16_SCAN", "false").lower() == "true"
AIS_UDP_LOCAL    = int(os.getenv("AIS_LOCAL_PORT", 10110))

b_bpf, a_bpf = butter(3, [235 / (FS_AUDIO / 2), 265 / (FS_AUDIO / 2)], btype='bandpass')
ZI_INIT = np.zeros(max(len(a_bpf), len(b_bpf)) - 1, dtype=float)

udp = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)


def rot(on):
    udp.sendto(b'1' if on else b'0', ("127.0.0.1", SWITCH_PORT))


def b2iq(raw):
    v = np.frombuffer(raw, dtype=np.uint8).astype(np.float32) - 127.5
    return (v[0::2] + 1j * v[1::2]) / 127.5


def shift_dec(iq, off):
    t = np.arange(len(iq), dtype=np.float32) / SAMPLE_RATE
    return resample_poly(iq * np.exp(-1j * 2 * np.pi * off * t), up=1, down=64)


def tone_phase(x, hz, fs):
    x = (x - x.mean()) * np.hanning(len(x))
    t = np.arange(len(x), dtype=np.float32) / fs
    v = np.sum(x * np.exp(-1j * 2 * np.pi * hz * t))
    snr = 20 * np.log10(np.abs(v) / (np.std(x) * np.sqrt(len(x)) + 1e-9))
    return float(np.angle(v)), float(snr)


def fm_discriminator(iq):
    return np.angle(iq[1:] * np.conj(iq[:-1]))


def start_ais():
    cmd = ["AIS-catcher", "-r", "cu8", "-", "-s", str(SAMPLE_RATE),
           "-u", "127.0.0.1", str(AIS_UDP_LOCAL)]
    p = os.getenv("AISHUB_PORT")
    if p: cmd += ["-u", "data.aishub.net", p]
    ip, port = os.getenv("MT_SERVER_IP"), os.getenv("MT_SERVER_PORT")
    if ip and port: cmd += ["-u", ip, port]
    proc = subprocess.Popen(cmd, stdin=subprocess.PIPE, bufsize=0, close_fds=True)
    try:
        os.set_blocking(proc.stdin.fileno(), False)
    except Exception:
        pass
    return proc


def open_sdr():
    sdr = RtlSdr()
    sdr.sample_rate = SAMPLE_RATE
    sdr.gain = 28.0
    sdr.center_freq = FREQ_AIS
    return sdr


def safe_read(sdr, nbytes, retries=3):
    for attempt in range(retries):
        try:
            return sdr.read_bytes(nbytes)
        except Exception as e:
            print("[USB] read error (%d/%d): %s" % (attempt+1, retries, e),
                  file=sys.stderr)
            time.sleep(0.05)
    return None


def measure(sdr, cal_off, invert):
    rot(True)
    buf = []
    n = 204800
    zi = ZI_INIT.copy()
    try:
        warm = safe_read(sdr, n)
        if warm is None:
            return None, 0.0
        ch16_w = shift_dec(b2iq(warm), OFFSET_CH16)
        _, zi = lfilter(b_bpf, a_bpf, fm_discriminator(ch16_w), zi=zi)

        for _ in range(11):
            raw = safe_read(sdr, n)
            if raw is None:
                return None, 0.0
            iq = b2iq(raw)
            ch16_fm = fm_discriminator(shift_dec(iq, OFFSET_CH16))
            ch16_f, zi = lfilter(b_bpf, a_bpf, ch16_fm, zi=zi)
            pilot_am = np.abs(shift_dec(iq, OFFSET_PILOT))
            ps, ss = tone_phase(ch16_f, ROTATION_HZ, FS_AUDIO)
            pr, sr = tone_phase(pilot_am, ROTATION_HZ, FS_AUDIO)
            if ss < 10.0:
                break
            if sr >= 14.0:
                d = (pr - ps) if invert else (ps - pr)
                buf.append(np.radians((np.degrees(d) + cal_off + 360.0) % 360.0))
    finally:
        rot(False)

    if len(buf) < 3:
        return None, 0.0
    mv = np.mean(np.exp(1j * np.array(buf)))
    return (round((np.degrees(np.angle(mv)) + 360.0) % 360.0, 1),
            round(float(np.abs(mv)), 2))


def run_multiplexer(cal_offset_deg=0.0, invert_sign=False, on_bearing_callback=None):
    ais = start_ais()
    sdr = open_sdr()
    rot(False)
    floor = None
    last = time.monotonic()
    last_alive = time.monotonic()
    print("[MUX] start", file=sys.stderr)
    try:
        while True:
            raw = safe_read(sdr, 131072)
            if raw is None:
                print("[MUX] re-open SDR after USB failure", file=sys.stderr)
                try: sdr.close()
                except Exception: pass
                time.sleep(1.0)
                sdr = open_sdr()
                last = time.monotonic()
                continue
            if ais.stdin:
                try:
                    ais.stdin.write(raw)
                except BlockingIOError:
                    pass
                except (BrokenPipeError, OSError) as e:
                    print("[AIS] pipe broken: %s" % e, file=sys.stderr)
                    try: ais.kill(); ais.wait(timeout=2)
                    except Exception: pass
                    ais = start_ais()
            now = time.monotonic()
            if now - last_alive > 30.0:
                if ais.poll() is not None:
                    print("[AIS] died - restart", file=sys.stderr)
                    try: ais.kill(); ais.wait(timeout=2)
                    except Exception: pass
                    ais = start_ais()
                last_alive = now
            if (now - last) >= 0.6 and (ENABLE_CH16_SCAN or on_bearing_callback):
                sdr.center_freq = FREQ_RDF
                _ = safe_read(sdr, 65536)
                sniff = safe_read(sdr, 102400)
                if sniff is not None:
                    ch16 = shift_dec(b2iq(sniff), OFFSET_CH16)
                    p = float(np.mean(np.abs(ch16) ** 2))
                    if floor is None: floor = p
                    else: floor = 0.92 * floor + 0.08 * min(p, floor * 1.5)
                    snr = 10 * np.log10((p + 1e-12) / (floor + 1e-12))
                    if snr >= SQUELCH_SNR_DB and on_bearing_callback:
                        b, q = measure(sdr, cal_offset_deg, invert_sign)
                        if b is not None and q >= 0.75:
                            try: on_bearing_callback(b, q)
                            except Exception as e:
                                print("[CALLBACK] %s" % e, file=sys.stderr)
                sdr.center_freq = FREQ_AIS
                _ = safe_read(sdr, 65536)
                last = time.monotonic()
    finally:
        rot(False)
        try: sdr.close()
        except Exception: pass
        try:
            ais.terminate()
            try: ais.wait(timeout=3)
            except subprocess.TimeoutExpired: ais.kill()
        except Exception: pass
