# app.py – Streamlit izdanje Wialon DDD Managera
import os, io, zipfile, json, requests, smtplib, re
from email.message import EmailMessage
from datetime import datetime, date
from dateutil import tz
import streamlit as st

UTC      = tz.tzutc()
DATE_RE  = re.compile(r"20\d{6}")
BASE_URL = "https://hst-api.wialon.com/wialon/ajax.html"

# ----- tajni podaci --------------------------------------------------------
TOKEN       = st.secrets.get("TOKEN")
SMTP_SERVER = st.secrets.get("SMTP_SERVER")
SMTP_PORT   = int(st.secrets.get("SMTP_PORT", 587))
SMTP_USER   = st.secrets.get("SMTP_USER")
SMTP_PASS   = st.secrets.get("SMTP_PASS")
RECIPS      = st.secrets.get("RECIPIENTS")

# Fallback – omogući ručni unos ako nema secrets (npr. lokalni test)
if not TOKEN:
    st.sidebar.warning("⚠️  Nema TOKEN u secrets-ima.")
    TOKEN = st.sidebar.text_input("Wialon token", type="password")
    st.sidebar.write("---")
if not SMTP_USER:
    st.sidebar.warning("⚠️  Nema SMTP postavki u secrets-ima.")

# ----- Wialon helpers ------------------------------------------------------
@st.cache_data(ttl=3600, show_spinner=False)      # 1 h
def login():
    r = requests.get(BASE_URL,
        params={"svc":"token/login",
                "params":json.dumps({"token":TOKEN})})
    j = r.json()
    if "error" in j:
        st.error(f"Login error: {j}")
        st.stop()
    return j["eid"]

@st.cache_data(ttl=900, show_spinner=False)       # 15 min
def get_vehicles(sid):
    r = requests.post(BASE_URL,
        data={"svc":"core/search_items",
              "params":json.dumps({
                  "spec":{"itemsType":"avl_unit","propName":"sys_name",
                          "propValueMask":"*","sortType":"sys_name"},
                  "force":1,"flags":1,"from":0,"to":0}),
              "sid":sid})
    j = r.json()
    if "error" in j:
        st.error(j); st.stop()
    return [{"id":it["id"],
             "name":it.get("nm","Unknown"),
             "reg": it.get("prp",{}).get("reg_number","")} for it in j["items"]]

def list_files(sid, vid, target:date):
    r = requests.post(BASE_URL,
        data={"svc":"file/list",
              "params":json.dumps({"itemId":vid,"storageType":2,
                                   "path":"tachograph/","mask":"*",
                                   "recursive":False,"fullPath":False}),
              "sid":sid})
    data=r.json(); out=[]
    for f in data:
        ct = datetime.fromtimestamp(f.get("ct",0), UTC).date()
        mt = datetime.fromtimestamp(f.get("mt",0), UTC).date()
        if ct==target or mt==target:
            out.append(f); continue
        m = DATE_RE.search(f["n"])
        if m and datetime.strptime(m.group(),"%Y%m%d").date()==target:
            out.append(f)
    return sorted(out, key=lambda x:x.get("mt",x.get("ct",0)), reverse=True)

def fetch_file(sid, vid, fname):
    p={"svc":"file/get",
       "params":json.dumps({"itemId":vid,"storageType":2,
                            "path":f"tachograph/{fname}"}),
       "sid":sid}
    return requests.get(BASE_URL, params=p).content

# ---------------- Streamlit UI --------------------------------------------
st.set_page_config("Wialon DDD Manager", layout="wide")
sid = login()
vehicles = get_vehicles(sid)

# ----- SIDEBAR (vozilo + datum) -------------------------------------------
st.sidebar.header("Vozila")

search = st.sidebar.text_input("Pretraga")
filtered = [v for v in vehicles if search.lower() in (v["reg"]+v["name"]).lower()]
if not filtered:
    st.sidebar.info("Nema rezultata.")
    st.stop()

choice = st.sidebar.radio(
    "Izaberi vozilo",
    options=filtered,
    format_func=lambda v: f'{v["reg"]} — {v["name"]}',
    index=0
)
pick_date = st.sidebar.date_input("Datum", value=date.today())

# ----- LISTA FAJLOVA -------------------------------------------------------
vid = choice["id"]
files = list_files(sid, vid, pick_date)
st.subheader(f"Fajlovi za **{choice['reg']}** – {pick_date.strftime('%d.%m.%Y')} "
             f"({len(files)})")

if not files:
    st.info("Nema fajlova za taj datum.")
    st.stop()

# -- Checkboxes (per-file) --------------------------------------------------
if "checked" not in st.session_state:
    st.session_state.checked = {}

cols = st.columns(3)
for idx, f in enumerate(files):
    key = f'chk_{f["n"]}'
    default = st.session_state.checked.get(key, False)
    checked = cols[idx % 3].checkbox(f["n"], value=default, key=key)
    st.session_state.checked[key] = checked

selected = [f["n"] for f in files if st.session_state.checked.get(f'chk_{f["n"]}')]

# ----- AKCIJE --------------------------------------------------------------
c1, c2 = st.columns(2)

with c1:
    st.markdown("### 📥 Download")
    disabled = len(selected)==0
    if st.button("Preuzmi ZIP", disabled=disabled):
        mem = io.BytesIO()
        with zipfile.ZipFile(mem, "w") as zf:
            for fn in selected:
                zf.writestr(fn, fetch_file(sid, vid, fn))
        st.download_button("Klikni za download",
                           data=mem.getvalue(),
                           mime="application/zip",
                           file_name=f"{choice['reg']}_{pick_date}.zip",
                           use_container_width=True)

with c2:
    st.markdown("### ✉️  Pošalji mail")
    if st.button("Pošalji", disabled=disabled or not SMTP_USER):
        try:
            buf = io.BytesIO()
            with zipfile.ZipFile(buf,"w") as zf:
                for fn in selected:
                    zf.writestr(fn, fetch_file(sid, vid, fn))
            msg = EmailMessage()
            msg["Subject"] = f"DDD fajlovi {choice['reg']} {pick_date}"
            msg["From"] = SMTP_USER
            msg["To"]   = RECIPS
            msg.set_content("Export iz Streamlit aplikacije")
            msg.add_attachment(buf.getvalue(),
                               maintype="application", subtype="zip",
                               filename=f"{choice['reg']}_{pick_date}.zip")
            with smtplib.SMTP(SMTP_SERVER, SMTP_PORT) as s:
                s.starttls(); s.login(SMTP_USER, SMTP_PASS); s.send_message(msg)
            st.success("Poslato!")
        except Exception as e:
            st.error(e)
