FROM python:3.11-slim-bookworm AS build

ARG AIS_CATCHER_VERSION=v0.71
ARG AIS_CATCHER_COMMIT=a3cb3bf8f30b462dff58c9b874dd9d932b45406a
WORKDIR /build

RUN apt-get update && apt-get install -y --no-install-recommends \
        build-essential cmake git pkg-config libsqlite3-dev \
        librtlsdr-dev libusb-1.0-0-dev libgpiod-dev \
    && rm -rf /var/lib/apt/lists/*

RUN git clone --depth 1 --branch "${AIS_CATCHER_VERSION}" \
        https://github.com/jvde-github/AIS-catcher.git /build/AIS-catcher \
    && test "$(git -C /build/AIS-catcher rev-parse HEAD)" = "${AIS_CATCHER_COMMIT}" \
    && cmake -S /build/AIS-catcher -B /build/AIS-catcher/build \
        -DRTLSDR=ON -DAIRSPY=OFF -DSDRPLAY=OFF -DAIRSPYHF=OFF \
        -DHACKRF=OFF -DHYDRASDR=OFF -DSOAPYSDR=OFF -DSOXR=OFF \
        -DSAMPLERATE=OFF -DZMQ=OFF -DPSQL=OFF -DSQLITE=OFF \
        -DOPENSSL=OFF -DZLIB=OFF -DNMEA2000=OFF \
        -DWEBVIEWER=OFF -DAPI=OFF -DDELIVERY=OFF \
    && cmake --build /build/AIS-catcher/build --parallel 2 \
    && cmake --install /build/AIS-catcher/build

COPY app/switch_daemon.c /build/switch_daemon.c
RUN cc -O2 -Wall -Wextra -Werror $(pkg-config --cflags libgpiod) \
        /build/switch_daemon.c -o /usr/local/sbin/yard-sdr-switch-daemon \
        $(pkg-config --libs libgpiod)

FROM python:3.11-slim-bookworm
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/opt/yard-sdr-node

RUN apt-get update && apt-get install -y --no-install-recommends \
        ca-certificates libgpiod2 librtlsdr0 libusb-1.0-0 \
        libsqlite3-0 libstdc++6 \
    && rm -rf /var/lib/apt/lists/*

COPY --from=build /usr/local/bin/AIS-catcher /usr/local/bin/AIS-catcher
COPY --from=build /usr/local/sbin/yard-sdr-switch-daemon \
    /usr/local/sbin/yard-sdr-switch-daemon
WORKDIR /opt/yard-sdr-node
COPY requirements-dev.txt .
RUN pip install --no-cache-dir -r requirements-dev.txt pyrtlsdr==0.3.0
COPY app ./app
COPY tools ./tools
WORKDIR /opt/yard-sdr-node/app
CMD ["python", "-u", "main.py"]
