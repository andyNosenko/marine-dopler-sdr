#!/usr/bin/env bash
set -Eeuo pipefail

REPO_ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
MODE=native
APPLY_BOOT_TUNING=false
AIS_CATCHER_TAG=v0.71
AIS_CATCHER_COMMIT=a3cb3bf8f30b462dff58c9b874dd9d932b45406a
BUILD_DIR=

cleanup_build_dir() {
    if [[ -n "$BUILD_DIR" && "$BUILD_DIR" == /tmp/yard-sdr-build.* &&
          -d "$BUILD_DIR" ]]; then
        rm -rf -- "$BUILD_DIR"
    fi
}
trap cleanup_build_dir EXIT

usage() {
    cat <<'EOF'
Usage:
  sudo ./install.sh [--native|--host-only] [--apply-boot-tuning]

--native             Install dependencies and enable native systemd services (default).
--host-only          Prepare chrony, GPIO/RTL-SDR udev rules and .env for Docker Compose.
--apply-boot-tuning  Back up /boot/armbianEnv.txt and add isolcpus=3.
                     A reboot is required; the script never reboots automatically.
EOF
}

while (($#)); do
    case "$1" in
        --native) MODE=native ;;
        --host-only) MODE=host-only ;;
        --apply-boot-tuning) APPLY_BOOT_TUNING=true ;;
        --help|-h) usage; exit 0 ;;
        *) echo "Unknown option: $1" >&2; usage >&2; exit 2 ;;
    esac
    shift
done

if [[ "$EUID" -ne 0 ]]; then
    echo "Run this script with sudo." >&2
    exit 1
fi

if [[ ! -f /etc/os-release ]]; then
    echo "Cannot identify the operating system (/etc/os-release missing)." >&2
    exit 1
fi
# shellcheck disable=SC1091
. /etc/os-release
if [[ "${ID:-}" != "armbian" && "${ID:-}" != "debian" &&
      " ${ID_LIKE:-} " != *"debian"* ]]; then
    echo "Supported target: Armbian/Debian 12 (bookworm); found ${PRETTY_NAME:-unknown}." >&2
    exit 1
fi
if [[ "${VERSION_CODENAME:-}" != "bookworm" && "${VERSION_ID:-}" != "12" ]]; then
    echo "Supported target: Debian 12 (bookworm); found ${VERSION_CODENAME:-${VERSION_ID:-unknown}}." >&2
    exit 1
fi
if [[ "$(uname -m)" != "aarch64" ]]; then
    echo "Supported target architecture: aarch64; found $(uname -m)." >&2
    exit 1
fi

ensure_group() {
    if ! getent group "$1" >/dev/null; then
        groupadd --system "$1"
    fi
}

install_host_packages() {
    apt-get update
    DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends \
        chrony udev
    ensure_group gpio
    ensure_group plugdev

    install -d -m 0755 /etc/udev/rules.d
    cat > /etc/udev/rules.d/60-yard-sdr.rules <<'EOF'
SUBSYSTEM=="gpio", KERNEL=="gpiochip1", GROUP="gpio", MODE="0660"
SUBSYSTEM=="usb", ATTR{idVendor}=="0bda", ATTR{idProduct}=="2838", GROUP="plugdev", MODE="0660"
EOF
    udevadm control --reload-rules
    systemctl enable --now chrony

    if [[ ! -e "$REPO_ROOT/.env" ]]; then
        install -m 0600 "$REPO_ROOT/.env.example" "$REPO_ROOT/.env"
        if [[ -n "${SUDO_USER:-}" ]]; then
            chown "$SUDO_USER" "$REPO_ROOT/.env"
        fi
        echo "Created $REPO_ROOT/.env from .env.example; review its values before use."
    fi

    if [[ "$APPLY_BOOT_TUNING" == true ]]; then
        apply_boot_tuning
    else
        echo "Boot tuning unchanged. Pass --apply-boot-tuning to back up and add isolcpus=3."
    fi
}

apply_boot_tuning() {
    local boot_env=/boot/armbianEnv.txt
    if [[ ! -f "$boot_env" ]]; then
        echo "Cannot tune CPU isolation: $boot_env not found; no boot file changed." >&2
        exit 1
    fi

    local backup tmp
    backup="${boot_env}.bak.$(date -u +%Y%m%dT%H%M%SZ)"
    while [[ -e "$backup" ]]; do backup="${backup}.1"; done
    cp --preserve=all "$boot_env" "$backup"

    tmp="$(mktemp /boot/armbianEnv.txt.XXXXXX)"
    awk '
        /^extraargs=/ {
            args = substr($0, 11)
            gsub(/(^|[[:space:]])isolcpus=[^[:space:]]+/, "", args)
            sub(/[[:space:]]+$/, "", args)
            printf "extraargs=%s%sisolcpus=3\n", args, args == "" ? "" : " "
            found = 1
            next
        }
        { print }
        END {
            if (!found) print "extraargs=isolcpus=3"
        }
    ' "$boot_env" > "$tmp"
    chown --reference="$boot_env" "$tmp"
    chmod --reference="$boot_env" "$tmp"
    mv "$tmp" "$boot_env"
    echo "Updated $boot_env; backup: $backup. Reboot is required (not performed)."
}

install_native() {
    DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends \
        build-essential cmake git pkg-config python3 python3-dev python3-venv \
        libsqlite3-dev librtlsdr-dev librtlsdr0 libusb-1.0-0 \
        libgpiod-dev libgpiod2

    local app_root
    app_root=/opt/yard-sdr-node
    BUILD_DIR="$(mktemp -d /tmp/yard-sdr-build.XXXXXX)"
    git clone --depth 1 --branch "$AIS_CATCHER_TAG" \
        https://github.com/jvde-github/AIS-catcher.git "$BUILD_DIR/AIS-catcher"
    if [[ "$(git -C "$BUILD_DIR/AIS-catcher" rev-parse HEAD)" != "$AIS_CATCHER_COMMIT" ]]; then
        echo "AIS-catcher tag $AIS_CATCHER_TAG did not resolve to the pinned commit." >&2
        exit 1
    fi
    cmake -S "$BUILD_DIR/AIS-catcher" -B "$BUILD_DIR/AIS-catcher/build" \
        -DRTLSDR=ON -DAIRSPY=OFF -DSDRPLAY=OFF -DAIRSPYHF=OFF \
        -DHACKRF=OFF -DHYDRASDR=OFF -DSOAPYSDR=OFF -DSOXR=OFF \
        -DSAMPLERATE=OFF -DZMQ=OFF -DPSQL=OFF -DSQLITE=OFF \
        -DOPENSSL=OFF -DZLIB=OFF -DNMEA2000=OFF \
        -DWEBVIEWER=OFF -DAPI=OFF -DDELIVERY=OFF
    cmake --build "$BUILD_DIR/AIS-catcher/build" --parallel 2
    cmake --install "$BUILD_DIR/AIS-catcher/build"

    cc -O2 -Wall -Wextra -Werror \
        $(pkg-config --cflags libgpiod) \
        "$REPO_ROOT/app/switch_daemon.c" \
        -o /usr/local/sbin/yard-sdr-switch-daemon \
        $(pkg-config --libs libgpiod)
    chmod 0755 /usr/local/sbin/yard-sdr-switch-daemon

    ensure_group yard-sdr
    install -d -m 0750 "$app_root/app" "$app_root/tools"
    cp -a "$REPO_ROOT/app/." "$app_root/app/"
    cp -a "$REPO_ROOT/tools/." "$app_root/tools/"
    install -m 0644 "$REPO_ROOT/requirements-dev.txt" "$app_root/"
    chown -R root:yard-sdr "$app_root"
    chmod -R o-rwx "$app_root"

    if ! id yard-sdr >/dev/null 2>&1; then
        useradd --system --home-dir /var/lib/yard-sdr --create-home \
            --shell /usr/sbin/nologin --gid yard-sdr \
            --groups plugdev,gpio yard-sdr
    else
        usermod --append --groups yard-sdr,plugdev,gpio yard-sdr
    fi
    install -d -o yard-sdr -g yard-sdr -m 0750 /var/lib/yard-sdr
    python3 -m venv "$app_root/.venv"
    "$app_root/.venv/bin/pip" install --upgrade pip
    "$app_root/.venv/bin/pip" install \
        -r "$app_root/requirements-dev.txt" pyrtlsdr==0.3.0
    chown -R root:yard-sdr "$app_root/.venv"
    chmod -R o-rwx "$app_root/.venv"

    if [[ ! -e /etc/yard-sdr-node.env ]]; then
        install -o root -g yard-sdr -m 0640 \
            "$REPO_ROOT/.env" /etc/yard-sdr-node.env
    fi

    install -d -m 0755 /etc/systemd/system
    sed "s|@APP_ROOT@|$app_root|g" \
        "$REPO_ROOT/systemd/yard-sdr-node.service" \
        > /etc/systemd/system/yard-sdr-node.service
    install -m 0644 "$REPO_ROOT/systemd/yard-sdr-switch.service" \
        /etc/systemd/system/yard-sdr-switch.service
    systemctl daemon-reload
    systemctl enable --now yard-sdr-switch.service
    systemctl enable --now yard-sdr-node.service
}

install_host_packages
if [[ "$MODE" == native ]]; then
    install_native
fi

cat <<EOF
Host preparation complete for Armbian/Debian 12 arm64.
EOF
if [[ "$MODE" == host-only ]]; then
    echo "Review $REPO_ROOT/.env before using Docker Compose."
    cat <<'EOF'
For Docker Compose, install Docker Engine with Compose v2, then run:
  docker compose up -d --build
EOF
else
    echo "Edit /etc/yard-sdr-node.env before using Telegram, provider lookup, or SMTP."
fi
