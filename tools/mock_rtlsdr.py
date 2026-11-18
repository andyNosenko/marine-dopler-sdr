"""Mock RtlSdr: синтетический IQ с 250 Hz FM на ch16 и 250 Hz AM на пилоте."""
import numpy as np

SAMPLE_RATE = 1024000
ROTATION_HZ = 250.0
OFFSET_CH16 = +400_000
OFFSET_PILOT = -400_000


class MockRtlSdr:
    def __init__(self, source_bearing_deg=0.0, *a, **kw):
        self.sample_rate = SAMPLE_RATE
        self.gain = 28.0
        self.center_freq = 162_000_000
        self.source_bearing_deg = source_bearing_deg
        self._t = 0.0

    def read_bytes(self, nbytes):
        n = nbytes // 2
        t = (self._t + np.arange(n)) / SAMPLE_RATE
        self._t += n / SAMPLE_RATE

        rot = 2 * np.pi * ROTATION_HZ * t.copy()

        # ch16: 250 Hz FM через фазовую коммутацию антенн
        slot = 1.0 / (4 * ROTATION_HZ)
        ant_idx = ((t.copy() / slot) % 4).astype(np.int32)
        ant_angles = np.radians([0.0, 90.0, 180.0, 270.0])
        source_bearing = np.radians(self.source_bearing_deg)
        phases = (np.pi / 2) * np.cos(source_bearing - ant_angles)
        phase_t = phases[ant_idx]
        ch16 = 0.5 * np.exp(1j * phase_t) * np.exp(
            1j * 2 * np.pi * OFFSET_CH16 * t.copy()
        )

        # пилот: 250 Hz AM (амплитуда 0..0.3) — сглаженная версия реального строба
        pilot_env = 0.15 * (1 + np.cos(rot))
        pilot = pilot_env * np.exp(1j * 2 * np.pi * OFFSET_PILOT * t.copy())

        noise = 0.002 * (np.random.randn(n) + 1j * np.random.randn(n))

        iq = ch16 + pilot + noise
        i = np.clip(iq.real * 127.5 + 127.5, 0, 255).astype(np.uint8)
        q = np.clip(iq.imag * 127.5 + 127.5, 0, 255).astype(np.uint8)
        raw = np.empty(nbytes, dtype=np.uint8)
        raw[0::2] = i
        raw[1::2] = q
        return raw.tobytes()

    def close(self):
        pass
