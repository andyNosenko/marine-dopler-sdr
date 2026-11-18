#!/usr/bin/env python3
"""Сквозная проверка DSP-цепочки measure() на разных азимутах."""
import os, sys, types
import numpy as np

SAMPLE_RATE  = 1024000
ROTATION_HZ  = 250.0
OFFSET_CH16  = +400_000
OFFSET_PILOT = -400_000
FS_AUDIO     = SAMPLE_RATE // 64

sys.path.insert(0, os.path.dirname(__file__))
from mock_rtlsdr import MockRtlSdr

# Подмена rtlsdr до импорта sdr_multiplexer
fake = types.ModuleType("rtlsdr")
fake.RtlSdr = MockRtlSdr
sys.modules["rtlsdr"] = fake

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "app"))
os.environ.setdefault("ENABLE_CH16_SCAN", "false")
import sdr_multiplexer as mux

mux.rot = lambda on: None


def angdiff(a: float, b: float) -> float:
    return abs((a - b + 180.0) % 360.0 - 180.0)


def main():
    print("--- 1. Диагностика SNR ---")
    sdr = MockRtlSdr(azimuth_deg=0.0)
    np.random.seed(42)
    iq = mux.b2iq(sdr.read_bytes(204800))
    ch16_fm = mux.fm_discriminator(mux.shift_dec(iq, OFFSET_CH16))
    ch16_f = mux.lfilter(mux.b_bpf, mux.a_bpf, ch16_fm)
    pilot_am = np.abs(mux.shift_dec(iq, OFFSET_PILOT))
    _, ss = mux.tone_phase(ch16_f, ROTATION_HZ, FS_AUDIO)
    _, sr = mux.tone_phase(pilot_am, ROTATION_HZ, FS_AUDIO)
    print(f"ch16 SNR : {ss:.1f} dB (need >= 10)")
    print(f"pilot SNR: {sr:.1f} dB (need >= 14)")
    assert ss >= 10.0 and sr >= 14.0, "SNR ниже порога!"

    print("\n--- 2. Калибровка на 0° ---")
    sdr0 = MockRtlSdr(azimuth_deg=0.0)
    np.random.seed(42)
    raw0, q0 = mux.measure(sdr0, cal_off=0.0, invert=False)
    assert raw0 is not None and q0 >= 0.9, f"Ошибка на 0°: {raw0}, q={q0}"
    cal_off = (360.0 - raw0) % 360.0
    print(f"Измерено без поправки: {raw0}° -> cal_off = {cal_off:.1f}° (quality={q0})")

    print("\n--- 3. Проверка точности на сетке углов ---")
    test_angles = [0.0, 45.0, 90.0, 135.0, 180.0, 270.0]
    for target_az in test_angles:
        sdr_t = MockRtlSdr(azimuth_deg=target_az)
        np.random.seed(42)
        b, q = mux.measure(sdr_t, cal_off=cal_off, invert=False)
        assert b is not None, f"None на угле {target_az}°"
        err = angdiff(b, target_az)
        print(f"Цель: {target_az:5.1f}° | Измерено: {b:5.1f}° | Ошибка: {err:4.1f}° | Quality: {q}")
        assert err <= 3.0, f"Слишком большая ошибка на {target_az}°: {err}°"
        assert q >= 0.9, f"Низкое качество на {target_az}°: {q}"

    print("\nOK: Вся DSP-цепочка и фазовая геометрия работают идеально!")


if __name__ == "__main__":
    main()
