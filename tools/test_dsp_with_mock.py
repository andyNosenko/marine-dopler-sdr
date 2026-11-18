#!/usr/bin/env python3
"""Проверка measure() на синтетическом IQ без реального RTL-SDR."""
import os, sys, types
import numpy as np

SAMPLE_RATE = 1024000
ROTATION_HZ = 250.0
OFFSET_CH16 = +400_000
OFFSET_PILOT = -400_000
FS_AUDIO = SAMPLE_RATE // 64
# The mock's slot/tone reference has a 45° offset and reversed phase sign.
MOCK_CAL_OFFSET_DEG = 45.0
MOCK_INVERT_SIGN = True

sys.path.insert(0, os.path.dirname(__file__))
from mock_rtlsdr import MockRtlSdr

# 1) Подмена rtlsdr до импорта sdr_multiplexer
fake = types.ModuleType("rtlsdr")
fake.RtlSdr = MockRtlSdr
sys.modules["rtlsdr"] = fake

# 2) Импорт sdr_multiplexer
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "app"))
os.environ.setdefault("ENABLE_CH16_SCAN", "false")
import sdr_multiplexer as mux

# 3) rot — no-op
mux.rot = lambda on: None

# 4) Диагностика: читаем один блок и смотрим ss/sr напрямую
print("--- diagnostics (single block) ---")
sdr = MockRtlSdr()
np.random.seed(42)
raw = sdr.read_bytes(204800)
iq = mux.b2iq(raw)
ch16_fm = mux.fm_discriminator(mux.shift_dec(iq, OFFSET_CH16))
ch16_f = mux.lfilter(mux.b_bpf, mux.a_bpf, ch16_fm)
pilot_am = np.abs(mux.shift_dec(iq, OFFSET_PILOT))
_, ss = mux.tone_phase(ch16_f, ROTATION_HZ, FS_AUDIO)
_, sr = mux.tone_phase(pilot_am, ROTATION_HZ, FS_AUDIO)
print(f"ch16 SNR: {ss:.1f} dB (need >= 10)")
print(f"pilot SNR: {sr:.1f} dB (need >= 14)")

# 5) Проверка истинного пеленга на разных азимутах
print("--- measure() ---")
for true_bearing in (0.0, 45.0, 90.0, 180.0, 270.0):
    sdr = MockRtlSdr(source_bearing_deg=true_bearing)
    np.random.seed(42)
    bearing, quality = mux.measure(
        sdr, cal_off=MOCK_CAL_OFFSET_DEG, invert=MOCK_INVERT_SIGN)
    print(f"True: {true_bearing}  Bearing: {bearing}  Quality: {quality}")

    if bearing is None:
        print(f"FAIL: measure() returned None for {true_bearing}°")
        sys.exit(1)
    error = abs(bearing - true_bearing)
    error = min(error, 360.0 - error)
    if error >= 15:
        print(
            f"FAIL: true={true_bearing} measured={bearing} err={error:.1f}°")
        sys.exit(1)
    if quality < 0.5:
        print(f"FAIL: quality too low: {quality} (expected >= 0.5)")
        sys.exit(1)
    sdr.close()
print("OK: DSP chain measures known synthetic bearings within 15°")
