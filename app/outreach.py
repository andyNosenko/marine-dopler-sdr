import html, os, sys, time, smtplib, requests
from email.message import EmailMessage
from target_manager import MIN_AGE, vessel_age

PROVIDER_API_KEY = os.getenv("PROVIDER_API_KEY", "")
SMTP_HOST = os.getenv("SMTP_HOST", "")
SMTP_PORT = int(os.getenv("SMTP_PORT", 465))
SMTP_USER = os.getenv("SMTP_USER", "")
SMTP_PASS = os.getenv("SMTP_PASS", "")
TG_TOKEN  = os.getenv("TELEGRAM_BOT_TOKEN", "")
TG_CHAT   = os.getenv("TELEGRAM_CHAT_ID", "")
TELEGRAM_API_URL = os.getenv(
    "TELEGRAM_API_URL", "https://api.telegram.org").rstrip("/")

def _safe_tok(tok):
    return tok[:6] + "..." + tok[-4:] if tok and len(tok) > 12 else "<none>"

def tg(text, buttons=None, retries=3):
    if not TG_TOKEN or not TG_CHAT:
        print("[TG] disabled (tok=%s)" % _safe_tok(TG_TOKEN), file=sys.stderr)
        return
    payload = {"chat_id": TG_CHAT, "text": text[:4000],
               "parse_mode": "HTML", "disable_web_page_preview": True}
    if buttons:
        payload["reply_markup"] = {"inline_keyboard": buttons}
    for i in range(retries):
        try:
            r = requests.post(
                "%s/bot%s/sendMessage" % (TELEGRAM_API_URL, TG_TOKEN),
                json=payload, timeout=10)
            if r.status_code == 200:
                return
            print("[TG] HTTP %d: %s" % (r.status_code, r.text[:150]),
                  file=sys.stderr)
            if r.status_code in (400, 403):
                return
        except requests.RequestException as e:
            print("[TG] %s" % e, file=sys.stderr)
        time.sleep(2 ** i)

def enrich(imo, mmsi):
    if not PROVIDER_API_KEY:
        return {}
    try:
        params = {"api-key": PROVIDER_API_KEY}
        params["imo" if imo else "mmsi"] = imo if imo else mmsi
        r = requests.get("https://api.datalastic.com/api/v0/vessel",
                         params=params, timeout=15)
        return (r.json().get("data") or {}) if r.status_code == 200 else {}
    except requests.RequestException as e:
        print("[API] %s" % e, file=sys.stderr); return {}

def draft_message(name, imo, length, year, bearing, dist):
    year_s = year if year and year > 1900 else "unknown"
    length_s = ("%s m" % length) if length else "n/a"
    return ("Dear Technical Superintendent / Fleet Manager,\n\n"
            "We noticed M/V %s (IMO: %s, LOA: %s, Built: %s) operating "
            "in our region (observed bearing %s deg, ~%s km).\n\n"
            "Our shipyard has an open dry-docking slot for vessels up to "
            "180 m LOA.\n\n"
            "Best regards,\nShipyard Commercial Department\n"
            % (name, imo, length_s, year_s, bearing, dist))

def send_manual(conn, lock, mmsi, bearing, dist, note=""):
    with lock:
        row = conn.execute(
            "SELECT imo, shipname, length, proposal_sent "
            "FROM vessels WHERE mmsi=?", (mmsi,)).fetchone()
    if not row:
        tg("[!] MMSI %s not found" % mmsi); return False
    imo, name, length, already_sent = row
    if already_sent:
        tg(html.escape("[i] Proposal for M/V %s already sent" % name))
        return False

    info = enrich(imo or 0, mmsi)
    year = info.get("year_built")
    age = vessel_age(year)
    if age < MIN_AGE:
        tg(html.escape(
            "[i] M/V %s (IMO %s) age=%s < MIN_AGE=%s, skip"
            % (name or "?", imo, age, MIN_AGE)))
        return False

    to = (info.get("manager_email") or info.get("owner_email") or "").strip()
    if not to:
        tg("[!] No email in API for MMSI %s" % mmsi); return False

    with lock:
        cur = conn.execute(
            "UPDATE vessels SET proposal_sent=1, proposal_pending_at=? "
            "WHERE mmsi=? AND proposal_sent=0", (time.time(), mmsi))
        conn.commit()
        if cur.rowcount == 0:
            tg(html.escape(
                "[i] Proposal for M/V %s claimed by another process" % name))
            return False

    msg = EmailMessage()
    msg["Subject"] = "Ship Repair & Dry-Docking - M/V %s (IMO %s)" % (name, imo)
    msg["From"] = SMTP_USER; msg["To"] = to
    body = draft_message(name, imo, length, year, bearing, dist)
    if note:
        body += "\n\n---\n%s\n" % note
    msg.set_content(body)
    try:
        with smtplib.SMTP_SSL(SMTP_HOST, SMTP_PORT, timeout=20) as s:
            s.login(SMTP_USER, SMTP_PASS)
            s.send_message(msg)
    except Exception as e:
        with lock:
            conn.execute(
                "UPDATE vessels SET proposal_sent=0, proposal_pending_at=NULL "
                "WHERE mmsi=?", (mmsi,))
            conn.commit()
        tg(html.escape("[X] SMTP error for MMSI %s: %s" % (mmsi, e)))
        return False

    with lock:
        conn.execute(
            "UPDATE vessels SET proposal_pending_at=NULL WHERE mmsi=?", (mmsi,))
        conn.commit()
    tg(html.escape(
        "[OK] Proposal sent: M/V %s (MMSI %s) -> %s" % (name, mmsi, to)))
    return True

def notify_candidates(bearing, quality, candidates):
    if not candidates:
        tg("[RDF] Bearing %s deg (q=%.2f) - no candidates."
           % (bearing, quality))
        return
    lines = ["[RDF] <b>Bearing %s deg</b> (q=%.2f) - candidates:"
             % (bearing, quality)]
    kb = []
    for c in candidates[:5]:
        tag = "[+]" if c["eligible"] else "[-]"
        sent_mark = " v" if c["proposal_sent"] else ""
        safe_name = html.escape(str(c["name"] or "?"))
        safe_reasons = html.escape(str(c["reasons"]))
        lines.append(
            "%s <b>%s</b>%s\n"
            "    MMSI <code>%s</code>, IMO %s, %s m\n"
            "    %s km, d=%s deg, conf=%s, age=%s s\n"
            "    <i>%s</i>"
            % (tag, safe_name, sent_mark, c["mmsi"],
               c["imo"] or "-", c["length"] or "?",
               c["dist_km"], c["delta_deg"], c["confidence"],
               c["age_s"], safe_reasons))
        if c["eligible"] and not c["proposal_sent"]:
            kb.append([{
                "text": "Send -> %s" % (c["name"] or c["mmsi"]),
                "callback_data": "send:%s:%s:%s"
                    % (c["mmsi"], bearing, c["dist_km"]),
            }])
    tg("\n".join(lines), buttons=kb or None)
