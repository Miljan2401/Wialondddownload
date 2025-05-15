# auto_sender.py – headless skript za GitHub Actions
import os, io, json, re, zipfile, requests, smtplib
from email.message import EmailMessage
from datetime import datetime, date, timedelta, timezone

# ----------------------------------------------------------------------------
BASE       = "https://hst-api.wialon.com/wialon/ajax.html"
TOKEN      = os.environ["TOKEN"]
SMTP_SRV   = os.environ["SMTP_SERVER"]
SMTP_PORT  = int(os.environ.get("SMTP_PORT", 587))
SMTP_USER  = os.environ["SMTP_USER"]
SMTP_PASS  = os.environ["SMTP_PASS"]
RECIPS     = [m.strip() for m in os.environ["RECIPIENTS"].split(",") if m.strip()]
DATE_RE    = re.compile(r"20\d{6}")        # hvata npr. 20250514 iz imena fajla
UTC        = timezone.utc
TARGET_DAY = (date.today() - timedelta(days=1))  # „jučerašnji“ (po UTC, vidi niže)
# Ako želiš striktno po beogradskom vremenu, koristi:
# from zoneinfo import ZoneInfo
# TARGET_DAY = datetime.now(ZoneInfo("Europe/Belgrade")).date() - timedelta(days=1)

# ----------------------------------------------------------------------------
def login():
    r = requests.get(
        BASE,
        params={"svc": "token/login", "params": json.dumps({"token": TOKEN})},
        timeout=15,
    )
    r.raise_for_status()
    return r.json()["eid"]

def get_vehicles(sid: str):
    payload = {
        "svc": "core/search_items",
        "params": json.dumps({
            "spec": {
                "itemsType": "avl_unit",
                "propName": "sys_name",
                "propValueMask": "*",
                "sortType": "sys_name",
            },
            "force": 1,
            "flags": 1,
            "from": 0,
            "to": 0,
        }),
        "sid": sid,
    }
    return requests.post(BASE, data=payload, timeout=20).json()["items"]

def list_files_for_day(sid: str, vid: int, target: date):
    payload = {
        "svc": "file/list",
        "params": json.dumps({
            "itemId": vid,
            "storageType": 2,
            "path": "tachograph/",
            "recursive": False,
        }),
        "sid": sid,
    }
    files = requests.post(BASE, data=payload, timeout=20).json()
    selected = []
    for f in files:
        ct = datetime.fromtimestamp(f.get("ct", 0), UTC).date()
        mt = datetime.fromtimestamp(f.get("mt", 0), UTC).date()
        if ct == target or mt == target:
            selected.append(f)
            continue
        m = DATE_RE.search(f["n"])
        if m and datetime.strptime(m.group(), "%Y%m%d").date() == target:
            selected.append(f)
    return selected

def fetch_file(sid: str, vid: int, name: str):
    params = {
        "svc": "file/get",
        "params": json.dumps({
            "itemId": vid,
            "storageType": 2,
            "path": f"tachograph/{name}",
        }),
        "sid": sid,
    }
    return requests.get(BASE, params=params, timeout=30).content

def send_zip(veh, zip_bytes: bytes):
    msg = EmailMessage()
    reg = veh.get("prp", {}).get("reg_number", "")
    msg["Subject"] = f"DDD fajlovi {reg} – {TARGET_DAY.strftime('%d.%m.%Y')}"
    msg["From"] = SMTP_USER
    msg["To"] = ", ".join(RECIPS)
    msg.set_content(
        "Automatski DDD export sa Wialon naloga.\n"
        f"Vozilo: {veh.get('nm')}\nDatum:  {TARGET_DAY.strftime('%d.%m.%Y')}"
    )
    fname = f"{reg or veh['nm']}_{TARGET_DAY}.zip"
    msg.add_attachment(zip_bytes, maintype="application", subtype="zip", filename=fname)

    with smtplib.SMTP(SMTP_SRV, SMTP_PORT, timeout=30) as s:
        s.starttls()
        s.login(SMTP_USER, SMTP_PASS)
        s.send_message(msg)

def run():
    sid = login()
    vehicles = get_vehicles(sid)
    for v in vehicles:
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as zf:
            for f in list_files_for_day(sid, v["id"], TARGET_DAY):
                zf.writestr(f["n"], fetch_file(sid, v["id"], f["n"]))
        if buf.tell():          # ima fajlova
            send_zip(v, buf.getvalue())

if __name__ == "__main__":
    run()
