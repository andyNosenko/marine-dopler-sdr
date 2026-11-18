#!/usr/bin/env python3
import os, sys, time, sqlite3, tempfile
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "app"))

os.environ.setdefault("YARD_LAT", "59.9343")
os.environ.setdefault("YARD_LON", "30.3351")
os.environ.setdefault("MAX_DIST_KM", "30")
os.environ.setdefault("SECTOR_DEG", "30")
os.environ.setdefault("MIN_VESSEL_LENGTH", "60")
os.environ.setdefault("MAX_VESSEL_LENGTH", "180")
os.environ.setdefault("FUSION_WINDOW_S", "30")
os.environ.setdefault("MIN_VESSEL_AGE", "12")

from target_manager import (find_candidates, extrapolate, vessel_age,
                            geo_bearing, geo_dist_km, angdiff)

PASS = FAIL = 0

def check(name, cond, extra=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  OK   {name}")
    else:
        FAIL += 1
        print(f"  FAIL {name}  {extra}")

def make_db():
    f = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    f.close()
    conn = sqlite3.connect(f.name)
    conn.execute("""CREATE TABLE vessels (
        mmsi INTEGER PRIMARY KEY, imo INTEGER, shipname TEXT, length INTEGER,
        lat REAL, lon REAL, last_seen REAL,
        prev_lat REAL, prev_lon REAL, prev_seen REAL,
        cog REAL, sog REAL, proposal_sent INTEGER DEFAULT 0)""")
    return conn, f.name

def insert(conn, mmsi, lat, lon, last_seen, cog=0, sog=0,
           imo=0, name="TEST", length=100):
    conn.execute("""INSERT INTO vessels
        (mmsi, lat, lon, last_seen, cog, sog, imo, shipname, length)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (mmsi, lat, lon, last_seen, cog, sog, imo, name, length))
    conn.commit()

print("\n[1] Geometry")
b = geo_bearing(0, 0, 0, 1)
check("geo_bearing east = 90", abs(b - 90) < 0.1, f"got {b}")
b = geo_bearing(0, 0, 1, 0)
check("geo_bearing north = 0", abs(b) < 0.1 or abs(b - 360) < 0.1, f"got {b}")
check("geo_dist ~111 km", 111 < geo_dist_km(0, 0, 1, 0) < 112)
check("angdiff(350, 10) = 20", abs(angdiff(350, 10) - 20) < 0.1)
check("angdiff(10, 350) = 20", abs(angdiff(10, 350) - 20) < 0.1)

print("\n[2] Extrapolation")
lat0, lon0 = 60.0, 30.0
lat1, lon1 = extrapolate(lat0, lon0, cog=90, sog=10, dt_s=3600)
check("10kn/h ~ 18.5 km",
      abs(geo_dist_km(lat0, lon0, lat1, lon1) - 18.52) < 0.5)
check("east - lon grows", lon1 > lon0)
lat2, lon2 = extrapolate(lat0, lon0, None, None, 100)
check("no cog/sog - unchanged", lat2 == lat0 and lon2 == lon0)

print("\n[3] vessel_age")
check("None -> -1", vessel_age(None) == -1)
check("0 -> -1", vessel_age(0) == -1)
check("1899 -> -1", vessel_age(1899) == -1)
check("2050 -> -1", vessel_age(2050) == -1)
check("2010 -> 14-16", 14 <= vessel_age(2010) <= 16)

print("\n[4] last_seen filter")
conn, path = make_db()
now = time.time()
# Координаты к северо-востоку от верфи: пеленг ~35°, попадает в сектор 45°
insert(conn, 111111111, 59.98, 30.40, now - 5)
insert(conn, 222222222, 59.98, 30.40, now - 60)
cand = find_candidates(conn, 45.0, bearing_time=now, now=now)
mm = [c["mmsi"] for c in cand]
check("fresh kept", 111111111 in mm)
check("stale cut", 222222222 not in mm)

print("\n[5] Distance cut")
conn.execute("DELETE FROM vessels")
insert(conn, 333333333, 60.30, 30.80, now - 5)
insert(conn, 444444444, 59.95, 30.4, now - 5)
cand = find_candidates(conn, 45.0, bearing_time=now, now=now)
mm = [c["mmsi"] for c in cand]
check("far cut", 333333333 not in mm)
check("near kept", 444444444 in mm)

print("\n[6] Sector")
conn.execute("DELETE FROM vessels")
insert(conn, 555555555, 60.0, 30.3351, now - 5)
cand = find_candidates(conn, 0.0, bearing_time=now, now=now)
check("bearing 0 to north passed", 555555555 in [c["mmsi"] for c in cand])
cand = find_candidates(conn, 90.0, bearing_time=now, now=now)
check("bearing 90 to north cut",
      555555555 not in [c["mmsi"] for c in cand])

print("\n[7] Confidence and sort")
conn.execute("DELETE FROM vessels")
insert(conn, 666666666, 60.0, 30.34, now - 2)
insert(conn, 777777777, 60.1, 30.50, now - 20)
cand = find_candidates(conn, 0.0, bearing_time=now, now=now)
check("candidates >= 1", len(cand) >= 1)
if len(cand) >= 2:
    check("top has max confidence",
          cand[0]["confidence"] >= cand[1]["confidence"])
    check("sorted desc",
          cand == sorted(cand, key=lambda v: -v["confidence"]))

print("\n[8] Eligible")
conn.execute("DELETE FROM vessels")
insert(conn, 888888888, 60.0, 30.34, now - 5, imo=0, length=100)
insert(conn, 999999999, 60.0, 30.34, now - 5, imo=9123456, length=40)
insert(conn, 100000000, 60.0, 30.34, now - 5, imo=9123457, length=100)
cand = find_candidates(conn, 0.0, bearing_time=now, now=now)
by_mmsi = {c["mmsi"]: c for c in cand}
check("no IMO not eligible",
      not by_mmsi.get(888888888, {}).get("eligible", True))
check("too short not eligible",
      not by_mmsi.get(999999999, {}).get("eligible", True))
check("OK eligible", by_mmsi.get(100000000, {}).get("eligible", False))

print("\n[9] Extrapolation in find_candidates")
conn.execute("DELETE FROM vessels")
insert(conn, 121212121, 60.0, 30.3351, now - 10,
       cog=90, sog=20, imo=9123456)
cand = find_candidates(conn, 0.0, bearing_time=now, now=now)
check("small motion still matches",
      121212121 in [c["mmsi"] for c in cand])

conn.close()
os.unlink(path)
print(f"\n{'='*40}")
print(f"PASS: {PASS}   FAIL: {FAIL}")
sys.exit(0 if FAIL == 0 else 1)
