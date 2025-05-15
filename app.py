# app.py – Wialon DDD Manager (SID-only, 2025-05-15)

import io, json, zipfile, re, smtplib, base64, requests, traceback
from email.message import EmailMessage
from datetime import datetime, date
from urllib.parse import unquote
from dateutil import tz
import streamlit as st

# ───────────────────────── Page config
st.set_page_config(page_title="Wialon DDD Manager", layout="wide")
UTC      = tz.tzutc()
DATE_RE  = re.compile(r"20\d{6}")           # YYYYMMDD u nazivu fajla

# ───────────────────────── Parametri iz URL-a
q          = st.query_params
SID        = q.get("sid")
BASE_URL   = unquote(q.get("baseUrl", "https://hst-api.wialon.com"))
USER_NAME  = q.get("user", "")
API_PATH   = f"{BASE_URL.rstrip('/')}/wialon/ajax.html"

if not SID:
    st.error("Pokreni aplikaciju iz Wialon-a (URL mora imati sid=…).")
    st.stop()

# ───────────────────────── SMTP & (opciono) GitHub toggle
SMTP_SERVER = st.secrets.get("SMTP_SERVER", "smtp.gmail.com")
SMTP_PORT   = int(st.secrets.get("SMTP_PORT", 587))
SMTP_USER   = st.secrets.get("SMTP_USER")
SMTP_PASS   = st.secrets.get("SMTP_PASS")
RECIPS_DEF  = st.secrets.get("RECIPIENTS", "")

GITHUB_PAT  = st.secrets.get("GITHUB_TOKEN")   # opciono
REPO        = st.secrets.get("GITHUB_REPO")    # opciono

# ───────────────────────── Helper: user-id trenutnog naloga
def get_user_id(username: str) -> int | None:
    payload = {
        "svc": "core/search_items",
        "params": json.dumps({
            "spec": {
                "itemsType": "avl_user",
                "propName": "sys_name",
                "propValueMask": username,
                "sortType": "sys_name",
            },
            "force": 1, "flags": 1, "from": 0, "to": 0}),
        "sid": SID,
    }
    res = requests.post(API_PATH, data=payload, timeout=10).json()
    if isinstance(res, dict) and res.get("items"):
        return res["items"][0]["id"]
    return None

MY_UID = get_user_id(USER_NAME)

# ───────────────────────── Vozila (creatorId → fallback sve)
@st.cache_data(ttl=900)
def get_vehicles():
    def _query(spec):
        payload = {"svc":"core/search_items",
                   "params":json.dumps({"spec":spec,"force":1,"flags":1,"from":0,"to":0}),
                   "sid":SID}
        res = requests.post(API_PATH, data=payload, timeout=15).json()
        if isinstance(res, dict) and res.get("error"):
            st.error(f"Wialon error {res['error']}"); st.stop()
        return res["items"] if isinstance(res, dict) else res

    units = _query({"itemsType":"avl_unit","propName":"creatorId",
                    "propValueMask": str(MY_UID),"sortType":"sys_name"})
    if not units:
        units = _query({"itemsType":"avl_unit","propName":"sys_name",
                        "propValueMask":"*","sortType":"sys_name"})

    return [{"id":u.get("id"),
             "name":u.get("nm","Unknown"),
             "reg":u.get("prp",{}).get("reg_number","")} for u in units]

# ───────────────────────── NOVI list_files (isti kao u .exe)
def list_files(vid: int, target_date: date):
    """Svi tachograph fajlovi za dati datum (ct/mt ili YYYYMMDD u nazivu)."""
    payload = {
        "svc": "file/list",
        "params": json.dumps({
            "itemId": vid,
            "storageType": 2,
            "path": "tachograph/",
            "mask": "*",
            "recursive": False,
            "fullPath": False,
        }),
        "sid": SID,
    }
    try:
        data = requests.post(API_PATH, data=payload, timeout=15).json()

        if isinstance(data, dict) and data.get("error"):
            if data["error"] != 4:          # 4 = folder ne postoji
                st.error(f"Wialon error {data['error']}")
            return []

        out = []
        for f in data:
            ct = datetime.fromtimestamp(f.get("ct", 0), UTC).date()
            mt = datetime.fromtimestamp(f.get("mt", 0), UTC).date()
            if ct == target_date or mt == target_date:
                out.append(f); continue
            m = DATE_RE.search(f["n"])
            if m and datetime.strptime(m.group(), "%Y%m%d").date() == target_date:
                out.append(f)
        return sorted(out, key=lambda x: x.get("mt", x.get("ct", 0)), reverse=True)

    except Exception as e:
        st.error(e)
        return []

def fetch_file(vid:int,name:str)->bytes:
    p={"svc":"file/get",
       "params":json.dumps({"itemId":vid,"storageType":2,
                            "path":f"tachograph/{name}"}),
       "sid":SID}
    return requests.get(API_PATH, params=p, timeout=30).content

# ───────────────────────── UI
st.sidebar.success(f"▶️ {USER_NAME or 'SID ' + SID[:4]+'…'}")
vehicles  = get_vehicles()
search    = st.sidebar.text_input("Pretraga")
pick_date = st.sidebar.date_input("Datum", value=date.today())

if "recips" not in st.session_state: st.session_state.recips = RECIPS_DEF
st.sidebar.text_area("Primaoci (zarez)", key="recips", height=80)

def toggle_auto(state:bool):
    if not (GITHUB_PAT and REPO): return
    pk = requests.get(f"https://api.github.com/repos/{REPO}/actions/secrets/public-key",
                      headers={"Authorization":f"token {GITHUB_PAT}"}).json()
    raw = b"true" if state else b"false"
    enc = base64.b64encode(bytes(a^b for a,b in zip(raw, base64.b64decode(pk["key"]))))
    requests.put(f"https://api.github.com/repos/{REPO}/actions/secrets/AUTO_ON",
                 json={"encrypted_value":enc.decode(),"key_id":pk["key_id"]},
                 headers={"Authorization":f"token {GITHUB_PAT}"})
st.sidebar.checkbox("Aktiviraj automatiku", on_change=toggle_auto, args=(True,))

filtered=[v for v in vehicles if search.lower() in (v["reg"]+v["name"]).lower()]
if not filtered: st.sidebar.info("Nema rezultata."); st.stop()
choice=st.sidebar.radio("Vozilo", filtered,
                        format_func=lambda v:f"{v['reg']} — {v['name']}")
vid=choice["id"]; files=list_files(vid, pick_date)

st.subheader(f"Fajlovi za **{choice['reg']}** – {pick_date:%d.%m.%Y} ({len(files)})")
if not files: st.info("Nema fajlova."); st.stop()

if "checked" not in st.session_state: st.session_state.checked={}
cols=st.columns(3)
for i,f in enumerate(files):
    key=f"chk_{f['n']}"
    st.session_state.checked[key]=cols[i%3].checkbox(
        f["n"], st.session_state.checked.get(key, False), key=key)

selected=[f["n"] for f in files if st.session_state.checked.get(f"chk_{f['n']}")]

left,right=st.columns(2)
with left:
    st.markdown("### 📥 Download")
    if st.button("Preuzmi ZIP", disabled=not selected):
        mem=io.BytesIO()
        with zipfile.ZipFile(mem,"w") as zf:
            for fn in selected: zf.writestr(fn, fetch_file(vid, fn))
        st.download_button("Klikni za download", mem.getvalue(), "application/zip",
                           f"{choice['reg']}_{pick_date}.zip", use_container_width=True)

with right:
    st.markdown("### ✉️ Pošalji mail")
    if st.button("Pošalji", disabled=not(selected and SMTP_USER)):
        try:
            buf=io.BytesIO()
            with zipfile.ZipFile(buf,"w") as zf:
                for fn in selected: zf.writestr(fn, fetch_file(vid, fn))
            msg=EmailMessage()
            msg["Subject"]=f"DDD fajlovi {choice['reg']} {pick_date:%d.%m.%Y}"
            msg["From"]=SMTP_USER; msg["To"]=st.session_state.recips
            msg.set_content("Export iz Streamlit aplikacije")
            msg.add_attachment(buf.getvalue(), maintype="application", subtype="zip",
                               filename=f"{choice['reg']}_{pick_date}.zip")
            with smtplib.SMTP(SMTP_SERVER, SMTP_PORT, timeout=30) as s:
                s.starttls(); s.login(SMTP_USER, SMTP_PASS); s.send_message(msg)
            st.success("Poslato!")
        except Exception as e:
            st.error(e)
