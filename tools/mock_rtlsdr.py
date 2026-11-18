import numpy as np

SAMPLE_RATE  = 1024000
ROTATION_HZ  = 250.0
OFFSET_CH16  = +400_000
OFFSET_PILOT = -400_000

class MockRtlSdr:
    def __init__(self, *args, azimuth_deg: float = 0.0, **kwargs):
        self.sample_rate = SAMPLE_RATE
        self.gain = 28.0
        self.center_freq = 162_000_000
        self.azimuth_deg = azimuth_deg
        self._idx = 0

    def read_bytes(self, nbytes: int) -> bytes:
        n = nbytes // 2
        idx = self._idx + np.arange(n, dtype=np.float64)
        self._idx += n
        t = idx / self.sample_rate

        slot = self.sample_rate / (4.0 * ROTATION_HZ)
        ant = ((idx // slot) % 4).astype(np.int32)
        angles = np.radians([0.0, 90.0, 180.0, 270.0])
        phases = 0.8 * np.cos(np.radians(self.azimuth_deg) - angles)

        ch16 = 0.20 * np.exp(1j * phases[ant]) * np.exp(1j * 2.0 * np.pi * OFFSET_CH16 * t)
        gate = ((idx % (self.sample_rate / ROTATION_HZ)) < slot).astype(np.float64)
        pilot = 0.25 * gate * np.exp(1j * 2.0 * np.pi * OFFSET_PILOT * t)
        noise = 0.002 * (np.random.randn(n) + 1j * np.random.randn(n))

        iq = ch16 + pilot + noise
        raw = np.empty(nbytes, dtype=np.uint8)
        raw[0::2] = np.clip(iq.real * 127.5 + 127.5, 0, 255).astype(np.uint8)
        raw[1::2] = np.clip(iq.imag * 127.5 + 127.5, 0, 255).astype(np.uint8)
        return raw.tobytes()

    def close(self):
        pass
