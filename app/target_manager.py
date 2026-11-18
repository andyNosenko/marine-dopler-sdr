import math, os, time
from datetime import datetime

YARD_LAT        = float(os.getenv("YARD_LAT", 59.9343))
YARD_LON        = float(os.getenv("YARD_LON", 30.3351))
MAX_DIST_KM     = float(os.getenv("MAX_DIST_KM", 30))
SECTOR_DEG      = float(os.getenv("SECTOR_DEG", 30))
MIN_LEN         = int(os.getenv("MIN_VESSEL_LENGTH", 60))
MAX_LEN         = int(os.getenv("MAX_VESSEL_LENGTH", 180))
MIN_AGE         = int(os.getenv("MIN_VESSEL_AGE", 12))
FUSION_WINDOW_S = float(os.getenv("FUSION_WINDOW_S", 30))

def geo_bearing(lat1, lon1, lat2, lon2):
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dl = math.radians(lon2 - lon1)
    y = math.sin(dl) * math.cos(p2)
    x = math.cos(p1) * math.sin(p2) - math.sin(p1) * math.cos(p2) * math.cos(dl)
    return (math.degrees(math.atan2(y, x)) + 360.0) % 360.0

def geo_dist_km(lat1, lon1, lat2, lon2):
    R = 6371.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = math.radians(lat2 - lat1), math.radians(lon2 - lon1)
    a = math.sin(dp/2)**2 + math.cos(p1)*math.cos(p2)*math.sin(dl/2)**2
    return R * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))

def angdiff(a, b):
    return abs((a - b + 180.0) % 360.0 - 180.0)

def extrapolate(lat, lon, cog, sog, dt_s):
    if cog is None or sog is None or dt_s <= 0:
        return lat, lon
    dist_km = sog * (dt_s / 3600.0) * 1.852
    brg = math.radians(cog)
    dlat = (dist_km / 111.32) * math.cos(brg)
    dlon = (dist_km / (111.32 * math.cos(math.radians(lat)) + 1e-9)) * math.sin(brg)
    return lat + dlat, lon + dlon

def find_candidates(conn, bearing, bearing_time=None, now=None):
    now = now or time.time()
    bearing_time = bearing_time or now
    cutoff = now - FUSION_WINDOW_S
    rows = conn.execute(
        "SELECT mmsi, imo, shipname, length, lat, lon, last_seen, "
        "       cog, sog, proposal_sent "
        "FROM vessels WHERE lat IS NOT NULL AND last_seen >= ?",
        (cutoff,)
    ).fetchall()

    out = []
    for mmsi, imo, name, length, lat, lon, last_seen, cog, sog, sent in rows:
        dt = bearing_time - last_seen
        lat_e, lon_e = extrapolate(lat, lon, cog, sog, dt)
        gb = geo_bearing(YARD_LAT, YARD_LON, lat_e, lon_e)
        d  = geo_dist_km(YARD_LAT, YARD_LON, lat_e, lon_e)
        delta = angdiff(bearing, gb)
        if d > MAX_DIST_KM or delta > SECTOR_DEG:
            continue

        age_s = now - last_seen
        conf = (max(0.0, 1.0 - delta / SECTOR_DEG)
                * max(0.0, 1.0 - age_s / FUSION_WINDOW_S)
                * max(0.2, 1.0 - d / MAX_DIST_KM))

        reasons = []
        if delta < 10: reasons.append("d<10")
        if age_s < 10: reasons.append("fresh")
        if d < 5:      reasons.append("close")
        if sog and sog > 0.5: reasons.append(f"SOG={sog}kn")
        if cog is not None:  reasons.append(f"COG={cog}deg")

        out.append({
            "mmsi": mmsi, "imo": imo, "name": name, "length": length,
            "geo_bearing": round(gb, 1), "dist_km": round(d, 2),
            "delta_deg": round(delta, 1), "age_s": round(age_s, 1),
            "cog": cog, "sog": sog,
            "confidence": round(conf, 2),
            "reasons": ",".join(reasons) or "none",
            "proposal_sent": bool(sent),
            "eligible": (imo or 0) > 0 and MIN_LEN <= (length or 0) <= MAX_LEN,
        })
    out.sort(key=lambda v: v["confidence"], reverse=True)
    return out

def vessel_age(year_built):
    if not year_built:
        return -1
    try:
        y = int(year_built)
        if y < 1900 or y > datetime.now().year:
            return -1
        return datetime.now().year - y
    except (TypeError, ValueError):
        return -1
    