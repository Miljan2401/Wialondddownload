# app.py – Wialon DDD Manager (v2.6 – query_params fix)

"""
• Ako se aplikacija otvori iz Wialon-a (URL sadrži ?sid= …), koristi prosleđeni SID
  i baseUrl – TOKEN tada nije potreban.
• U sidebar-u se mogu menjati primaoci mejla i paliti/gašiti GitHub automatika
  (secret AUTO_ON).
"""

import os, io, zipfile, json, requests, smtplib, re, base64
from email.message import EmailMessage
from datetime import datetime, date
from dateutil import tz
import streamlit as st

# ────────────────────────────────────────────────────────────────────────────
#  1. PAGE CONFIG – mora biti prvi st.* poziv
# ────────────────────────────────────────────────────────────────────────────
st.set_page_config(page_title="Wialon DDD Manager", layout="wide")

# ────────────────────────────────────────────────────────────────────────────
#  2. KONSTANTE
# ────────────────────────────────────────────────────────────────────────────
UTC      = tz.tzutc()
DATE_RE  = re.compile(r"20\d{6}")              # hvata YYYYMMDD u imenu fajla

# ────────────────────────────────────────────────────────────────────────────
#  3. PARAMETRI IZ URL-a (kada se pokrene kao Web-app unutar Wialon-a)
# ────────────────────────────────────────────────────────────────────────────
q            = st.query_params                # Mapping[str, str]
SID_IN_URL   = q.get("sid") or None
BASE_URL     = q.get("baseUrl") or "https://hst-api.wialon.com"
USER_LABEL   = q.get("user", "")
API_PATH     = f"{BASE_URL.rstrip('/')}/wialon/ajax.html"

# ────────────────────────────────────────────────────────────────────────────
#  4. SECRETS / ENV
# ────────────────────────────────────────────────────────────────────────────
TOKEN       = st.secrets.get("TOKEN")         # koristi se kad nema SID-a
SMTP_SERVER = st.secrets.get("SMTP_SERVER", "smtp.gmail.com")
SMTP_PORT   = int(st.secrets.get("SMTP_PORT", 587))
SMTP_USER   = st.secrets.get("SMTP_USER")
SMTP_PASS   = st.secrets.get("SMTP_PASS")
RECIPS_DEF  = st.secrets.get("RECIPIENTS", "")

GITHUB_PAT  = st.secrets.get("GITHUB_TOKEN")  # PAT sa scope=repo
REPO        = st.secrets.get("GITHUB_REPO")   # npr. "Miljan2401/WialonDDDdownload"

# ────────────────────────────────────────────────────────────────────────────
#  5. WIALON LOGIN
# ────────────────────────────────────────────────────────────────────────────
@st.cache_data(ttl=3600, show_spinner=False)
def login_by_token(tok: str) -> str:
    r = requests.get(
        API_PATH,
        params={"svc": "token/login", "params": json.dumps({"token": tok})},
        timeout=15,
    )
    j = r.json()
    if "error" in j:
        st.error(j)
        st.stop()
    return j["eid"]

if SID_IN_URL:
    sid = SID_IN_URL
    st.sidebar.success(f"▶️ Prijavljen: {USER_LABEL}")
else:
    if not TOKEN:
        TOKEN = st.sidebar.text_input("Wialon token", type="password")
    sid = login_by_token(TOKEN)

# ────────────────────────────────────────────────────────────────────────────
#  6. API HELPERS
# ────────────────────────────────────────────────────────────────────────────
@st.cache_data(ttl=900)
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
    j = requests.post(API_PATH, data=payload, timeout=20).json()
    if "error" in j:
        st.error(j)
        st.stop()
    return [
        {
            "id": it["id"],
            "name": it.get("nm", "Unknown"),
            "reg": it.get("prp", {}).get("reg_number", ""),
        }
        for it in j["items"]
    ]

def list_files(sid: str, vid: int, target: date):
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
    files = requests.post(API_PATH, data=payload, timeout=20).json()
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
    return requests.get(API_PATH, params=params, timeout=30).content

# ────────────────────────────────────────────────────────────────────────────
#  7. SIDEBAR UI
# ────────────────────────────────────────────────────────────────────────────
vehicles   = get_vehicles(sid)
search     = st.sidebar.text_input("Pretraga")
pick_date  = st.sidebar.date_input("Datum", value=date.today())

if "recips" not in st.session_state:
    st.session_state.recips = RECIPS_DEF
st.sidebar.text_area("Primaoci (zarez između)", key="recips", height=80)

# === GitHub toggle ==========================================================
def toggle_auto(state: bool):
    if not (GITHUB_PAT and REPO):
        st.warning("GitHub PAT ili REPO nedostaje u secrets-ima.")
        return
    # Public-key
    pk = requests.get(
        f"https://api.github.com/repos/{REPO}/actions/secrets/public-key",
        headers={"Authorization": f"token {GITHUB_PAT}"},
    ).json()
    key_id     = pk["key_id"]
    public_key = base64.b64decode(pk["key"])
    val        = b"true" if state else b"false"
    enc        = base64.b64encode(bytes(a ^ b for a, b in zip(val, public_key))).decode()
    requests.put(
        f"https://api.github.com/repos/{REPO}/actions/secrets/AUTO_ON",
        json={"encrypted_value": enc, "key_id": key_id},
        headers={"Authorization": f"token {GITHUB_PAT}"},
    )

auto_on = st.sidebar.checkbox("Aktiviraj automatiku", on_change=toggle_auto, args=(True,))
if not auto_on:
    st.sidebar.button("Deaktiviraj", on_click=toggle_auto, args=(False,))

# ────────────────────────────────────────────────────────────────────────────
#  8. LISTA FAJLOVA
# ────────────────────────────────────────────────────────────────────────────
filtered = [v for v in vehicles if search.lower() in (v["reg"] + v["name"]).lower()]
if not filtered:
    st.sidebar.info("Nema rezultata.")
    st.stop()

choice = st.sidebar.radio(
    "Izaberi vozilo",
    options=filtered,
    format_func=lambda v: f"{v['reg']} — {v['name']}",
)
vid   = choice["id"]
files = list_files(sid, vid, pick_date)

st.subheader(f"Fajlovi za **{choice['reg']}** – {pick_date:%d.%m.%Y} ({len(files)})")
if not files:
    st.info("Nema fajlova.")
    st.stop()

if "checked" not in st.session_state:
    st.session_state.checked = {}

cols = st.columns(3)
for i, f in enumerate(files):
    key = f"chk_{f['n']}"
    st.session_state.checked[key] = cols[i % 3].checkbox(
        f["n"], st.session_state.checked.get(key, False), key=key
    )

selected = [f["n"] for f in files if st.session_state.checked.get(f"chk_{f['n']}")]

# ────────────────────────────────────────────────────────────────────────────
#  9. AKCIJE
# ────────────────────────────────────────────────────────────────────────────
c1, c2 = st.columns(2)

with c1:
    st.markdown("### 📥 Download")
    if st.button("Preuzmi ZIP", disabled=not selected):
        mem = io.BytesIO()
        with zipfile.ZipFile(mem, "w") as zf:
            for fn in selected:
                zf.writestr(fn, fetch_file(sid, vid, fn))
        st.download_button(
            "Klikni za download",
            data=mem.getvalue(),
            mime="application/zip",
            file_name=f"{choice['reg']}_{pick_date}.zip",
            use_container_width=True,
        )

with c2:
    st.markdown("### ✉️ Pošalji mail")
    if st.button("Pošalji", disabled=not (selected and SMTP_USER)):
        try:
            buf = io.BytesIO()
            with zipfile.ZipFile(buf, "w") as zf:
                for fn in selected:
                    zf.writestr(fn, fetch_file(sid, vid, fn))
            msg = EmailMessage()
            msg["Subject"] = f"DDD fajlovi {choice['reg']} {pick_date:%d.%m.%Y}"
            msg["From"]    = SMTP_USER
            msg["To"]      = st.session_state.recips
            msg.set_content("Export iz Streamlit aplikacije")
            msg.add_attachment(
                buf.getvalue(),
                maintype="application",
                subtype="zip",
                filename=f"{choice['reg']}_{pick_date}.zip",
            )
            with smtplib.SMTP(SMTP_SERVER, SMTP_PORT, timeout=30) as s:
                s.starttls()
                s.login(SMTP_USER, SMTP_PASS)
                s.send_message(msg)
            st.success("Poslato!")
        except Exception as e:
            st.error(e)
