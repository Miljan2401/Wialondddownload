# app.py – Streamlit izdanje Wialon DDD Managera (v2 – SID iz URL‑a, uređivanje primaoca, toggle automatike)
"""
Ključne novine
--------------
1. Ako je aplikacija pozvana sa …/app/?sid=…&baseUrl=… koristi prosleđeni SID i BASE_URL
   (dakle, pokretanje iz Wialon App Center‑a *ne zahteva* TOKEN u secrets‑u).
2. Recipients (primaoci) se mogu menjati u sidebar‑u i čuvaju se u `st.session_state`.
   • Ako želiš trajnu memoriju – ubaci ih u `st.secrets` ili u GitHub secret RECIPIENTS.
3. Checkbox „Aktiviraj automatiku“ poziva GitHub REST API i pali/gasi secret `AUTO_ON`.
   • U repo secrets dodaj `GITHUB_TOKEN` (classic PAT sa `repo` scope‑om)
   • Ako je secret AUTO_ON="true" – GitHub Actions workflow šalje fajlove.
"""
import os, io, zipfile, json, requests, smtplib, re, base64
from email.message import EmailMessage
from datetime import datetime, date
from dateutil import tz
import streamlit as st

# ---------------------------------------------------------------------------
#  KONFIG
# ---------------------------------------------------------------------------
UTC = tz.tzutc()
DATE_RE = re.compile(r"20\d{6}")

# --- uzmi parametre iz URL‑a ------------------------------------------------
q = st.experimental_get_query_params()
SID_IN_URL   = q.get("sid", [None])[0]
BASE_URL     = q.get("baseUrl", ["https://hst-api.wialon.com"])[0]
USER_LABEL   = q.get("user", [""])[0]

# --- tajni podaci (koriste se samo kad SID nije prosleđen) ------------------
TOKEN       = st.secrets.get("TOKEN")
SMTP_SERVER = st.secrets.get("SMTP_SERVER", "smtp.gmail.com")
SMTP_PORT   = int(st.secrets.get("SMTP_PORT", 587))
SMTP_USER   = st.secrets.get("SMTP_USER")
SMTP_PASS   = st.secrets.get("SMTP_PASS")
RECIPS_DEF  = st.secrets.get("RECIPIENTS", "")
GITHUB_PAT  = st.secrets.get("GITHUB_TOKEN")   # classic token (repo scope)
REPO        = st.secrets.get("GITHUB_REPO")     # npr. "Miljan2401/WialonDDDdownload"

# ---------------------------------------------------------------------------
#  SID / LOGIN
# ---------------------------------------------------------------------------
@st.cache_data(ttl=3600, show_spinner=False)
def login_by_token(token: str):
    params = {"svc": "token/login", "params": json.dumps({"token": token})}
    r = requests.get(BASE_URL + "/wialon/ajax.html", params=params, timeout=15)
    j = r.json();
    if "error" in j:
        st.error(f"Login error: {j}")
        st.stop()
    return j["eid"]

if SID_IN_URL:
    sid = SID_IN_URL
    st.sidebar.success(f"▶️ Prijavljen korisnik: {USER_LABEL}")
else:
    if not TOKEN:
        st.sidebar.warning("⚠️  Nema TOKEN‑a u secrets‑ima. Unesi ga ručno.")
        TOKEN = st.sidebar.text_input("Wialon token", type="password")
    sid = login_by_token(TOKEN)

# ---------------------------------------------------------------------------
#  WIALON helpers
# ---------------------------------------------------------------------------
@st.cache_data(ttl=900, show_spinner=False)
def get_vehicles(sid: str):
    data = {
        "svc": "core/search_items",
        "params": json.dumps({
            "spec": {"itemsType": "avl_unit", "propName": "sys_name", "propValueMask": "*", "sortType": "sys_name"},
            "force": 1, "flags": 1, "from": 0, "to": 0}),
        "sid": sid
    }
    j = requests.post(BASE_URL + "/wialon/ajax.html", data=data, timeout=15).json()
    if "error" in j:
        st.error(j); st.stop()
    return [{"id": it["id"], "name": it.get("nm", "Unknown"),
             "reg": it.get("prp", {}).get("reg_number", "")} for it in j["items"]]

def list_files(sid: str, vid: int, target: date):
    data = {
        "svc": "file/list",
        "params": json.dumps({"itemId": vid, "storageType": 2, "path": "tachograph/", "recursive": False}),
        "sid": sid
    }
    files = requests.post(BASE_URL + "/wialon/ajax.html", data=data, timeout=15).json()
    out = []
    for f in files:
        ct = datetime.fromtimestamp(f.get("ct", 0), UTC).date()
        mt = datetime.fromtimestamp(f.get("mt", 0), UTC).date()
        if ct == target or mt == target:
            out.append(f); continue
        m = DATE_RE.search(f["n"])
        if m and datetime.strptime(m.group(), "%Y%m%d").date() == target:
            out.append(f)
    return sorted(out, key=lambda x: x.get("mt", x.get("ct", 0)), reverse=True)

def fetch_file(sid: str, vid: int, fname: str):
    p = {"svc": "file/get",
         "params": json.dumps({"itemId": vid, "storageType": 2, "path": f"tachograph/{fname}"}),
         "sid": sid}
    return requests.get(BASE_URL + "/wialon/ajax.html", params=p, timeout=30).content

# ---------------------------------------------------------------------------
#  STREAMLIT UI
# ---------------------------------------------------------------------------
st.set_page_config("Wialon DDD Manager", layout="wide")
vehicles = get_vehicles(sid)

st.sidebar.header("Vozila")
search = st.sidebar.text_input("Pretraga")
pick_date = st.sidebar.date_input("Datum", value=date.today())

# --- PRIMAOCE (editable) ----------------------------------------------------
if "recips" not in st.session_state:
    st.session_state.recips = RECIPS_DEF

st.sidebar.text_area("Primaoci (zarez između)", height=80, key="recips")

# --- AUTOMATIKA toggle ------------------------------------------------------
def set_auto_state(state: bool):
    if not (GITHUB_PAT and REPO):
        st.warning("GitHub token ili repo nije definisan u secrets‑ima.")
        return
    # Upali/ugasi secret AUTO_ON putem GitHub REST‑a
    url_pk = f"https://api.github.com/repos/{REPO}/actions/secrets/public-key"
    pk_r  = requests.get(url_pk, headers={"Authorization": f"token {GITHUB_PAT}"})
    pk_r.raise_for_status()
    pk_json = pk_r.json(); key_id = pk_json["key_id"]; public_key = base64.b64decode(pk_json["key"])
    # encrypt value (simple XOR + base64 zbog ograničenja – za demo, produkcija → nacl)
    val = "true" if state else "false"
    enc = base64.b64encode(bytes(a ^ b for a, b in zip(val.encode(), public_key))).decode()
    sec_url = f"https://api.github.com/repos/{REPO}/actions/secrets/AUTO_ON"
    req = {
        "encrypted_value": enc,
        "key_id": key_id
    }
    put_r = requests.put(sec_url, json=req, headers={"Authorization": f"token {GITHUB_PAT}"})
    if put_r.ok:
        st.toast("Status automatike ažuriran.")
    else:
        st.error(f"Greška: {put_r.status_code} – {put_r.text}")

auto_on = st.sidebar.checkbox("Aktiviraj automatiku", value=False, on_change=set_auto_state, args=(True,))
if not auto_on:
    st.sidebar.button("Deaktiviraj", on_click=set_auto_state, args=(False,))

# ---------------------------------------------------------------------------
#  FILTAR + LISTA FAJLOVA
# ---------------------------------------------------------------------------
filtered = [v for v in vehicles if search.lower() in (v["reg"] + v["name"]).lower()]
if not filtered:
    st.sidebar.info("Nema rezultata.")
    st.stop()

choice = st.sidebar.radio("Izaberi vozilo",
                          options=filtered,
                          format_func=lambda v: f"{v['reg']} — {v['name']}")
vid = choice["id"]
files = list_files(sid, vid, pick_date)

st.subheader(f"Fajlovi za **{choice['reg']}** – {pick_date:%d.%m.%Y} ({len(files)})")
if not files:
    st.info("Nema fajlova za taj datum."); st.stop()

# --- checkbox lista ---------------------------------------------------------
if "checked" not in st.session_state:
    st.session_state.checked = {}

cols = st.columns(3)
for idx, f in enumerate(files):
    key = f'chk_{f["n"]}'
    default = st.session_state.checked.get(key, False)
    checked = cols[idx % 3].checkbox(f["n"], value=default, key=key)
    st.session_state.checked[key] = checked

selected = [f["n"] for f in files if st.session_state.checked.get(f'chk_{f["n"]}')]

# ---------------------------------------------------------------------------
#  AKCIJE
# ---------------------------------------------------------------------------
c1, c2 = st.columns(2)

with c1:
    st.markdown("### 📥 Download")
    if st.button("Preuzmi ZIP", disabled=not selected):
        mem = io.BytesIO()
        with zipfile.ZipFile(mem, "w") as zf:
            for fn in selected:
                zf.writestr(fn, fetch_file(sid, vid, fn))
        st.download_button("Klikni za download", data=mem.getvalue(), mime="application/zip",
                           file_name=f"{choice['reg']}_{pick_date}.zip", use_container_width=True)

with c2:
    st.markdown("### ✉️  Pošalji mail")
    if st.button("Pošalji", disabled=not (selected and SMTP_USER)):
        try:
            buf = io.BytesIO()
            with zipfile.ZipFile(buf, "w") as zf:
                for fn in selected:
                    zf.writestr(fn, fetch_file(sid, vid, fn))
            msg = EmailMessage()
            msg["Subject"] = f"DDD fajlovi {choice['reg']} {pick_date:%d.%m.%Y}"
            msg["From"] = SMTP_USER
            msg["To"]   = st.session_state.recips
            msg.set_content("Export iz Streamlit aplikacije")
            msg.add_attachment(buf.getvalue(), maintype="application", subtype="zip",
                               filename=f"{choice['reg']}_{pick_date}.zip")
            with smtplib.SMTP(SMTP_SERVER, SMTP_PORT) as s:
                s.starttls()
                s.login(SMTP_USER, SMTP_PASS)
                s.send_message(msg)
            st.success("Poslato!")
        except Exception as e:
            st.error(e)
