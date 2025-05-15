# app.py – Wialon DDD Manager (v2.4 – sve string zagrade ispravne, fajl testiran bez SyntaxError-a)
"""Streamlit aplikacija kojoj se može proslediti SID iz Wialon-a ili koristiti TOKEN.
U sidebar-u se dodaju primaoci i pali/gasi GitHub automatika.
"""

import os, io, zipfile, json, requests, smtplib, re, base64
from email.message import EmailMessage
from datetime import datetime, date
from dateutil import tz
import streamlit as st

# 1️⃣ Page-config mora prvi
st.set_page_config(page_title="Wialon DDD Manager", layout="wide")

# 2️⃣ Konstante
UTC = tz.tzutc()
DATE_RE = re.compile(r"20\d{6}")

# 3️⃣ Parametri iz URL-a (za App Center)
q = st.experimental_get_query_params()
SID_IN_URL = q.get("sid", [None])[0]
BASE_URL   = q.get("baseUrl", ["https://hst-api.wialon.com"])[0]
USER_LABEL = q.get("user", [""])[0]
API_PATH   = f"{BASE_URL}/wialon/ajax.html"

# 4️⃣ Secrets
TOKEN       = st.secrets.get("TOKEN")
SMTP_SERVER = st.secrets.get("SMTP_SERVER", "smtp.gmail.com")
SMTP_PORT   = int(st.secrets.get("SMTP_PORT", 587))
SMTP_USER   = st.secrets.get("SMTP_USER")
SMTP_PASS   = st.secrets.get("SMTP_PASS")
RECIPS_DEF  = st.secrets.get("RECIPIENTS", "")
GITHUB_PAT  = st.secrets.get("GITHUB_TOKEN")
REPO        = st.secrets.get("GITHUB_REPO")

# 5️⃣ Wialon login
@st.cache_data(ttl=3600, show_spinner=False)
def login_by_token(tok: str):
    r = requests.get(API_PATH, params={"svc":"token/login","params":json.dumps({"token":tok})}, timeout=15)
    j = r.json();
    if "error" in j:
        st.error(j); st.stop()
    return j["eid"]

if SID_IN_URL:
    sid = SID_IN_URL
    st.sidebar.success(f"▶️ {USER_LABEL}")
else:
    if not TOKEN:
        TOKEN = st.sidebar.text_input("Wialon token", type="password")
    sid = login_by_token(TOKEN)

# 6️⃣ Helpers
@st.cache_data(ttl=900)
def get_vehicles(sid):
    q = {"svc":"core/search_items","params":json.dumps({
        "spec":{"itemsType":"avl_unit","propName":"sys_name","propValueMask":"*","sortType":"sys_name"},
        "force":1,"flags":1,"from":0,"to":0}),"sid":sid}
    j = requests.post(API_PATH, data=q, timeout=20).json();
    if "error" in j: st.error(j); st.stop()
    return [{"id":i["id"],"name":i.get("nm","Unknown"),"reg":i.get("prp",{}).get("reg_number","")} for i in j["items"]]

def list_files(sid, vid, target):
    q = {"svc":"file/list","params":json.dumps({"itemId":vid,"storageType":2,"path":"tachograph/","recursive":False}),"sid":sid}
    files = requests.post(API_PATH, data=q, timeout=20).json(); sel=[]
    for f in files:
        ct=datetime.fromtimestamp(f.get("ct",0),UTC).date(); mt=datetime.fromtimestamp(f.get("mt",0),UTC).date()
        if ct==target or mt==target: sel.append(f); continue
        m=DATE_RE.search(f["n"]);
        if m and datetime.strptime(m.group(),"%Y%m%d").date()==target: sel.append(f)
    return sorted(sel, key=lambda x:x.get("mt",x.get("ct",0)), reverse=True)

def fetch_file(sid, vid, name):
    p={"svc":"file/get","params":json.dumps({"itemId":vid,"storageType":2,"path":f"tachograph/{name}"}),"sid":sid}
    return requests.get(API_PATH, params=p, timeout=30).content

# 7️⃣ Sidebar UI
vehicles = get_vehicles(sid)
search = st.sidebar.text_input("Pretraga")
pick_date = st.sidebar.date_input("Datum", value=date.today())
if "recips" not in st.session_state:
    st.session_state.recips = RECIPS_DEF
st.sidebar.text_area("Primaoci (zarez)", key="recips", height=80)

# GitHub toggle

def toggle_auto(state: bool):
    if not (GITHUB_PAT and REPO):
        st.warning("Nema GitHub PAT/REPO u secrets."); return
    pk=requests.get(f"https://api.github.com/repos/{REPO}/actions/secrets/public-key",headers={"Authorization":f"token {GITHUB_PAT}"}).json()
    key_id=pk["key_id"]; pub=base64.b64decode(pk["key"]); val=b"true" if state else b"false"
    enc=base64.b64encode(bytes(a^b for a,b in zip(val,pub))).decode()
    requests.put(f"https://api.github.com/repos/{REPO}/actions/secrets/AUTO_ON",json={"encrypted_value":enc,"key_id":key_id},headers={"Authorization":f"token {GITHUB_PAT}"})

auto_on=st.sidebar.checkbox("Aktiviraj automatiku", on_change=toggle_auto, args=(True,))
if not auto_on:
    st.sidebar.button("Deaktiviraj", on_click=toggle_auto, args=(False,))

# 8️⃣ Lista fajlova
filtered=[v for v in vehicles if search.lower() in (v["reg"]+v["name"]).lower()]
if not filtered: st.sidebar.info("Nema rezultata."); st.stop()
choice=st.sidebar.radio("Izaberi vozilo", options=filtered, format_func=lambda v:f"{v['reg']} — {v['name']}")
vid=choice["id"]
files=list_files(sid, vid, pick_date)

st.subheader(f"Fajlovi za **{choice['reg']}** – {pick_date:%d.%m.%Y} ({len(files)})")
if not files: st.info("Nema fajlova."); st.stop()

if "checked" not in st.session_state: st.session_state.checked={}
cols=st.columns(3)
for i,f in enumerate(files):
    key=f"chk_{f['n']}"; st.session_state.checked[key]=cols[i%3].checkbox(f["n"], st.session_state.checked.get(key, False), key=key)
selected=[f["n"] for f in files if st.session_state.checked.get(f"chk_{f['n']}")]

# 9️⃣ Akcije
c1,c2=st.columns(2)
with c1:
    st.markdown("### 📥 Download")
    if st.button("Preuzmi ZIP", disabled=not selected):
        mem=io.BytesIO();
        with zipfile.ZipFile(mem,"w") as zf:
            for fn in selected: zf.writestr(fn, fetch_file(sid, vid, fn))
        st.download_button("Klikni za download", mem.getvalue(), "application/zip", f"{choice['reg']}_{pick_date}.zip", use_container_width=True)

with c2:
    st.markdown("### ✉️ Pošalji mail")
    if st.button("Pošalji", disabled=not(selected and SMTP_USER)):
        try:
            buf=io.BytesIO();
            with zipfile.ZipFile(buf,"w") as zf:
                for fn in selected: zf.writestr(fn, fetch_file(sid, vid, fn))
            msg=EmailMessage(); msg["Subject"]=f"DDD fajlovi {choice['reg']} {pick_date:%d.%m.%Y}"; msg["From"]=SMTP_USER; msg["To"]=st.session_state.recips
            msg.set_content("Export iz Streamlit aplikacije")
            msg.add_attachment(buf.getvalue(), maintype="application", subtype="zip", filename=f"{choice['reg']}_{pick_date}.zip")
            with smtplib.SMTP(SMTP_SERVER, SMTP_PORT, timeout=30) as s:
                s.starttls(); s.login(SMTP_USER, SMTP_PASS); s.send_message(msg)
            st.success("Poslato!")
        except Exception as e:
            st.error(e)
