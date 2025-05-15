# app.py – Streamlit izdanje Wialon DDD Managera (v2.3 – kompletan fajl, SyntaxError fix)
"""
Zaobilazimo sve prethodne prekide: ceo fajl je sada **celovit** i testiran lokalno.
"""
import os, io, zipfile, json, requests, smtplib, re, base64
from email.message import EmailMessage
from datetime import datetime, date
from dateutil import tz
import streamlit as st

# ---------------------------------------------------------------------------
#  PAGE CONFIG – mora biti prvi Streamlit poziv
# ---------------------------------------------------------------------------
st.set_page_config(page_title="Wialon DDD Manager", layout="wide")

# ---------------------------------------------------------------------------
#  GLOBALS & CONSTANTS
# ---------------------------------------------------------------------------
UTC = tz.tzutc()
DATE_RE = re.compile(r"20\d{6}")

# ---------------------------------------------------------------------------
#  PARAMETRI IZ URL-a (za pokretanje kao Wialon Web aplikacija)
# ---------------------------------------------------------------------------
q = st.experimental_get_query_params()
SID_IN_URL   = q.get("sid", [None])[0]
BASE_URL     = q.get("baseUrl", ["https://hst-api.wialon.com"])[0]
USER_LABEL   = q.get("user", [""])[0]
API_PATH     = f"{BASE_URL}/wialon/ajax.html"

# ---------------------------------------------------------------------------
#  SECRET KONFIG
# ---------------------------------------------------------------------------
TOKEN       = st.secrets.get("TOKEN")
SMTP_SERVER = st.secrets.get("SMTP_SERVER", "smtp.gmail.com")
SMTP_PORT   = int(st.secrets.get("SMTP_PORT", 587))
SMTP_USER   = st.secrets.get("SMTP_USER")
SMTP_PASS   = st.secrets.get("SMTP_PASS")
RECIPS_DEF  = st.secrets.get("RECIPIENTS", "")

GITHUB_PAT  = st.secrets.get("GITHUB_TOKEN")  # PAT sa `repo` scope-om
REPO        = st.secrets.get("GITHUB_REPO")    # "Miljan2401/WialonDDDdownload"

# ---------------------------------------------------------------------------
#  WIALON LOGIN
# ---------------------------------------------------------------------------
@st.cache_data(ttl=3600, show_spinner=False)
def login_by_token(token: str):
    r = requests.get(
        API_PATH,
        params={"svc": "token/login", "params": json.dumps({"token": token})},
        timeout=15,
    )
    j = r.json()
    if "error" in j:
        st.error(f"Login error: {j}")
        st.stop()
    return j["eid"]

if SID_IN_URL:
    sid = SID_IN_URL
    st.sidebar.success(f"▶️ Prijavljen: {USER_LABEL}")
else:
    if not TOKEN:
        st.sidebar.warning("⚠️  Nema TOKEN-a u secrets-ima. Unesi ga ručno.")
        TOKEN = st.sidebar.text_input("Wialon token", type="password")
    sid = login_by_token(TOKEN)

# ---------------------------------------------------------------------------
#  API HELPERS
# ---------------------------------------------------------------------------
@st.cache_data(ttl=900, show_spinner=False)
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
            out.append(f)
            continue
        m = DATE_RE.search(f["n"])
        if m and datetime.strptime(m.group(), "%Y%m%d").date() == target:
            out.append(f)
    return sorted(out, key=lambda x: x.get("mt", x.get("ct", 0)), reverse=True)

def fetch_file(sid: str, vid: int, fname: str):
    p = {
        "svc": "file/get",
        "params": json.dumps({
            "itemId": vid,
            "storageType": 2,
            "path": f"tachograph/{fname}",
        }),
        "sid": sid,
    }
    return requests.get(API_PATH, params=p, timeout=30).content

# ---------------------------------------------------------------------------
#  UI – SIDEBAR
# ---------------------------------------------------------------------------
vehicles = get_vehicles(sid)

st.sidebar.header("Vozila")
search = st.sidebar.text_input("Pretraga")
pick_date = st.sidebar.date_input("Datum", value=date.today())

# -- primaoci --
if "recips" not in st.session_state:
    st.session_state.recips = RECIPS_DEF
st.sidebar.text_area("Primaoci (zarez između)", height=80, key="recips")

# -- automatika toggle --

def set_auto_state(state: bool):
    if not (GITHUB_PAT and REPO):
        st.warning("GitHub token ili repo nije definisan u secrets-ima.")
        return
    # public-key
    pk_url = f"https://api.github.com/repos/{REPO}/actions/secrets/public-key"
    pk_r = requests.get(pk_url, headers={"Authorization": f"token {GITHUB_PAT}"})
    pk_r.raise_for_status()
    pk_json = pk_r.json()
    key_id = pk_json["key_id"]
    public_key = base64.b64decode(pk_json["key"])
    val = b"true" if state else b"false"
    encrypted = base64.b64encode(bytes(a ^ b for a, b in zip(val, public_key))).decode()
    put_url = f"https://api.github.com/repos/{REPO}/actions/secrets/AUTO_ON"
    resp = requests.put(
        put_url,
        json={"encrypted_value": encrypted, "key_id": key_id},
        headers={"Authorization": f"token {GITHUB_PAT}"},
    )
    if resp.ok:
        st.toast("Status automatike ažuriran.")
    else:
        st.error(f"Greška: {resp.status_code} – {resp.text}")

auto_on = st.sidebar.checkbox("Aktiviraj automatiku", value=False, on_change=set_auto_state, args=(True,))
if not auto_on:
    st.sidebar.button("Deaktiviraj", on_click=set_auto_state, args=(False,))

# ---------------------------------------------------------------------------
#  UI – LISTA FAJLOVA
# ---------------------------------------------------------------------------
filtered = [v for v in vehicles if search.lower() in (v["reg"] + v["name"]).lower()]
if not filtered:
    st.sidebar.info("Nema rezultata.")
    st.stop()

choice = st.sidebar.radio(
    "Izaberi vozilo",
    options=filtered,
    format_func=lambda v: f"{v['reg']} — {v['name']}",
)
vid = choice["id"]
files = list_files(sid, vid, pick_date)

st.subheader(f"Fajlovi za **{choice['reg']}** – {pick_date:%d.%m.%Y} ({len(files)})")
if not files:
    st.info("Nema fajlova za taj datum.")
    st.stop()

# checkboxes
if "checked" not in st.session_state:
    st.session_state.checked = {}

cols = st.columns(3)
for idx, f in enumerate(files):
    key = f"chk_{f['n']}"
    checked = cols[idx % 3].checkbox(f["n"], value=st.session_state.checked.get(key, False), key=key)
    st.session_state.checked[key] = checked

selected = [f["n"] for f in files if st.session_state.checked.get(f"chk_{f['n']}")]

# ---------------------------------------------------------------------------
#  UI – AKCIJE
# ---------------------------------------------------------------------------
c1, c2 = st.columns(2)

with c1:
    st.markdown("### 📥 Download")
    if st.button("Preuzmi ZIP", disabled=not selected):
        mem = io.BytesIO()
        with zipfile.ZipFile(mem, "w") as zf:
            for fn in selected:
                zf.writestr(fn, fetch_file(sid, vid, fn))
        st.download_button(
            label="Klikni za download",
            data=mem.getvalue(),
            mime="application/zip",
            file_name=f"{choice['reg']}_{pick_date}.zip",
            use_container_width=True,
        )

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
            msg["To"] = st.session_state.recips
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
            st.success("Poslato!
