# Deployment

Supported target: Orange Pi Zero 3 running Armbian/Debian 12 (bookworm), arm64.
The deployment uses AIS-catcher `v0.71`, pinned to commit
`a3cb3bf8f30b462dff58c9b874dd9d932b45406a`.

The antenna switch wiring is configured for `/dev/gpiochip1`: A=73 (header pin
7), B=70 (pin 11), GATE=69 (pin 13), all active-high. The switch daemon listens
on UDP `127.0.0.1:4210`, matching `app/sdr_multiplexer.py`.

## Native systemd installation

From the repository checkout:

```sh
sudo ./install.sh --native
```

The installer installs chrony, udev rules, AIS-catcher, Python dependencies and
the GPIO daemon under `/opt/yard-sdr-node`, then enables `yard-sdr-switch` and
`yard-sdr-node`. It creates `.env` from `.env.example` and copies it to
`/etc/yard-sdr-node.env` only when those files do not already exist. For native
service credentials, edit `/etc/yard-sdr-node.env` before relying on Telegram,
provider lookup, or SMTP.

To also add `isolcpus=3` to `/boot/armbianEnv.txt`, run:

```sh
sudo ./install.sh --native --apply-boot-tuning
```

This makes a timestamped backup before changing the boot file. Reboot manually
to apply the change; the installer does not reboot the board. If the file is
missing or the OS/architecture is unsupported, the installer stops rather than
guessing another boot configuration.

## Docker Compose

Install Docker Engine with Compose v2 separately, then prepare host access:

```sh
sudo ./install.sh --host-only
```

Review `.env`, then build and start both containers:

```sh
docker compose up -d --build
```

Compose uses host networking for the loopback UDP control/AIS sockets, passes
the USB bus to the SDR service, and passes `gpiochip1` only to the switch
daemon. The SQLite database is persisted under `./data/`. The `--host-only`
installer does not install Docker.

## Clock and hardware limits

The application waits for NTP synchronization when `timedatectl` is available.
If it is unavailable, startup requires the system clock to be at least
2024-01-01 UTC. With no RTC and no network time source, startup waits rather
than recording timestamps against an obviously invalid clock.

Mock DSP, calibration and E2E checks do not validate actual RF performance,
antenna wiring, USB stability, or GPIO timing on the Orange Pi. Verify the
switch truth table and RF path on the assembled hardware before unattended use.
