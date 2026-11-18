#!/usr/bin/env python3
import argparse, socket, time, sys, os
import yaml
from pyais.encode import encode_dict

AIS_PORT = int(os.getenv("AIS_LOCAL_PORT", "10110"))
UDP_TARGET = ("127.0.0.1", AIS_PORT)


def _to_sentences(encoded):
    """Нормализует вывод encode_dict в list[bytes] с \\r\\n."""
    if isinstance(encoded, (list, tuple)):
        items = list(encoded)
    else:
        items = [encoded]
    out = []
    for s in items:
        if isinstance(s, str):
            s = s.encode("ascii", "ignore")
        if not s.endswith(b"\r\n"):
            s = s + b"\r\n"
        out.append(s)
    return out


def send_nmea(sock, encoded):
    """Отправляет все части одним UDP-датаграмом (multipart склеивается)."""
    parts = _to_sentences(encoded)
    sock.sendto(b"".join(parts), UDP_TARGET)


def position_type1(mmsi, lat, lon, cog, sog):
    return encode_dict({
        "msg_type": 1, "mmsi": mmsi, "status": 0, "turn": 0,
        "speed": float(sog), "accuracy": 1,
        "lon": float(lon), "lat": float(lat),
        "course": float(cog), "heading": 511, "second": 0,
        "maneuver": 0, "raim": 0, "radio": 0,
    })


def position_type18(mmsi, lat, lon, cog, sog):
    return encode_dict({
        "msg_type": 18, "mmsi": mmsi, "reserved": 0,
        "speed": float(sog), "accuracy": 1,
        "lon": float(lon), "lat": float(lat),
        "course": float(cog), "heading": 511, "second": 0,
        "cs": 0, "display": 0, "dsc": 0, "band": 0,
        "msg22": 0, "raim": 0, "radio": 0,
    })


def static_type5(mmsi, imo, name, to_bow=80, to_stern=20):
    return encode_dict({
        "msg_type": 5, "mmsi": mmsi, "imo": imo,
        "callsign": "TST", "shipname": name, "ship_type": 70,
        "to_bow": to_bow, "to_stern": to_stern,
        "to_port": 5, "to_starboard": 5, "epfd": 0,
        "eta_month": 1, "eta_day": 1, "eta_hour": 0, "eta_minute": 0,
        "draught": 5.0, "destination": "TEST", "dte": 0,
    })


def run(scenario):
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    vessels = scenario["vessels"]
    step_s = float(scenario.get("step_s", 1.0))
    duration_s = float(scenario.get("duration_s", 60))

    for v in vessels:
        if v.get("imo") and v.get("name"):
            try:
                nmea = static_type5(v["mmsi"], v["imo"], v["name"],
                                    v.get("to_bow", 80), v.get("to_stern", 20))
                send_nmea(sock, nmea)
                print("[AIS] static  MMSI=%s '%s'" % (v["mmsi"], v["name"]))
            except Exception as e:
                print("[AIS] static error %s: %s" % (v["mmsi"], e),
                      file=sys.stderr)

    n_steps = int(duration_s / step_s)
    for t in range(n_steps):
        for v in vessels:
            v["lat"] += v.get("dlat_per_s", 0) * step_s
            v["lon"] += v.get("dlon_per_s", 0) * step_s
            try:
                if v.get("class", "A") == "B":
                    nmea = position_type18(v["mmsi"], v["lat"], v["lon"],
                                           v.get("cog", 0), v.get("sog", 0))
                else:
                    nmea = position_type1(v["mmsi"], v["lat"], v["lon"],
                                          v.get("cog", 0), v.get("sog", 0))
                send_nmea(sock, nmea)
            except Exception as e:
                print("[AIS] pos error %s: %s" % (v["mmsi"], e),
                      file=sys.stderr)
        if t % 10 == 0:
            print("[AIS] t=%4.0fs  ships=%d" % (t*step_s, len(vessels)))
        time.sleep(step_s)

    print("[AIS] done: %d steps x %ss" % (n_steps, step_s))


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--scenario", default="tools/scenarios/basic.yaml")
    args = ap.parse_args()
    with open(args.scenario) as f:
        run(yaml.safe_load(f))
