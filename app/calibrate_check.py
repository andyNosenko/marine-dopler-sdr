import time, socket, sys
import numpy as np
from rtlsdr import RtlSdr
from sdr_multiplexer import (
    SAMPLE_RATE, FREQ_RDF, OFFSET_PILOT, ROTATION_HZ,
    FS_AUDIO, shift_dec, tone_phase, measure
)

def check_pilot():
    """Проверка SNR пилот-тона 156.000 МГц."""
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.sendto(b'1', ("127.0.0.1", 4210)); time.sleep(0.2)
    sdr = RtlSdr()
    sdr.sample_rate = SAMPLE_RATE
    sdr.center_freq = FREQ_RDF
    sdr.gain = 28.0
    try:
        iq = sdr.read_samples(256000)
        pilot_am = np.abs(shift_dec(iq, OFFSET_PILOT))
        _, snr = tone_phase(pilot_am, ROTATION_HZ, FS_AUDIO)
        print(f"Pilot SNR: {snr:.1f} dB")
        if snr < 15:    print("Слабо: уменьшить аттенюатор на 10–20 дБ")
        elif snr > 45:  print("Сильно: увеличить аттенюатор на 10–20 дБ")
        else:           print("Норма (20–35 дБ оптимально)")
    finally:
        s.sendto(b'0', ("127.0.0.1", 4210)); sdr.close()

def calibrate_offset(known_azimuth_deg, num_measurements=20):
    """Калибровка RDF_CAL_OFFSET по известному источнику."""
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.sendto(b'1', ("127.0.0.1", 4210)); time.sleep(0.2)
    sdr = RtlSdr()
    sdr.sample_rate = SAMPLE_RATE
    sdr.center_freq = FREQ_RDF
    sdr.gain = 28.0

    offsets = []
    for _ in range(num_measurements):
        b, q = measure(sdr, cal_off=0.0, invert=False)
        if b is not None and q >= 0.85:
            delta = ((b - known_azimuth_deg + 180.0) % 360.0) - 180.0
            offsets.append(delta)
        time.sleep(0.1)

    sdr.close()
    s.sendto(b'0', ("127.0.0.1", 4210))

    if len(offsets) < 10:
        print(f"Недостаточно данных: {len(offsets)}/10+")
        return None
    mean_off = float(np.mean(offsets))
    std_off = float(np.std(offsets))
    print(f"RDF_CAL_OFFSET = {mean_off:.1f}° (std = {std_off:.1f}°)")
    if std_off > 15:
        print("ВНИМАНИЕ: разброс > 15°. Проверьте multipath.")
    return mean_off

if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "offset":
        known = float(sys.argv[2]) if len(sys.argv) > 2 else 0.0
        calibrate_offset(known)
    else:
        check_pilot()
