import argparse, os, time, socket, sys
import numpy as np
from sdr_multiplexer import (
    SAMPLE_RATE, FREQ_RDF, OFFSET_PILOT, ROTATION_HZ,
    FS_AUDIO, shift_dec, tone_phase, measure
)

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))

def _sdr_factory(mock=False, source_bearing_deg=0.0):
    if mock:
        if REPO_ROOT not in sys.path:
            sys.path.insert(0, REPO_ROOT)
        from tools.mock_rtlsdr import MockRtlSdr
        return lambda: MockRtlSdr(source_bearing_deg=source_bearing_deg)

    from rtlsdr import RtlSdr
    return RtlSdr

def check_pilot(sdr_factory=None):
    """Проверка SNR пилот-тона 156.000 МГц."""
    sdr_factory = sdr_factory or _sdr_factory()
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.sendto(b'1', ("127.0.0.1", 4210)); time.sleep(0.2)
    sdr = sdr_factory()
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

def calibrate_offset(known_azimuth_deg, num_measurements=20,
                     sdr_factory=None):
    """Калибровка RDF_CAL_OFFSET по известному источнику."""
    sdr_factory = sdr_factory or _sdr_factory()
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.sendto(b'1', ("127.0.0.1", 4210)); time.sleep(0.2)
    sdr = sdr_factory()
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
    parser = argparse.ArgumentParser(description="RTL-SDR pilot and RDF calibration")
    parser.add_argument("mode", nargs="?", choices=("pilot", "offset"),
                        default="pilot")
    parser.add_argument("known_azimuth", nargs="?", type=float, default=0.0)
    parser.add_argument("--mock", action="store_true",
                        help="use synthetic IQ instead of an RTL-SDR")
    parser.add_argument("--mock-bearing", type=float,
                        help="source bearing for mock IQ (default: known azimuth)")
    args = parser.parse_args()
    mock_bearing = args.mock_bearing
    if mock_bearing is None:
        mock_bearing = args.known_azimuth if args.mode == "offset" else 0.0
    sdr_factory = _sdr_factory(args.mock, mock_bearing)
    if args.mode == "offset":
        calibrate_offset(args.known_azimuth, sdr_factory=sdr_factory)
    else:
        check_pilot(sdr_factory=sdr_factory)
