# app.py – Wialon DDD Manager (SID-only, admin gore desno) – 2025-05-15
import io, json, zipfile, re, smtplib, base64, requests
from email.message import EmailMessage
from datetime import datetime, date
from urllib.parse import unquote
from pathlib import Path
from dateutil import tz
import streamlit as st

# ───── META
st.set_page_config("Wialon DDD Manager", layout="wide")
UTC, DATE_RE = tz.tzutc(), re.compile(r"20\d{6}")
DATA_FILE = Path("users.json")

# ───── URL parametri
q          = st.query_params
SID        = q.get("sid")
BASE_URL   = unquote(q.get("baseUrl", "https://hst-api.wialon.com"))
USER_NAME  = q.get("user", "")
ADMIN_FLAG = q.get("admin")         # ?admin=PIN
API_PATH   = f"{BASE_URL.rstrip('/')}/wialon/ajax.html"
if not SID: st.stop("Pokreni iz Wialon-a (nedostaje sid=…).")

# ───── secrets
SMTP_SERVER = st.secrets.get("SMTP_SERVER", "smtp.gmail.com")
SMTP_PORT   = int(st.secrets.get("SMTP_PORT", 587))
SMTP_USER   = st.secrets.get("SMTP_USER")
SMTP_PASS   = st.secrets.get("SMTP_PASS")
ADMIN_PIN   = st.secrets.get("ADMIN_PIN", "12345")   # promeni po želji

# ───── baza
def load_db() -> dict:  return json.loads(DATA_FILE.read_text()) if DATA_FILE.exists() else {}
def save_db(db):        DATA_FILE.write_text(json.dumps(db, indent=2))

# ───── helper: userId trenutnog naloga
def get_uid(name:str)->int|None:
    p={"svc":"core/search_items","params":json.dumps({
        "spec":{"itemsType":"avl_user","propName":"sys_name",
                "propValueMask":name,"sortType":"sys_name"},
        "force":1,"flags":1,"from":0,"to":0}),"sid":SID}
    js=requests.post(API_PATH,data=p,timeout=10).json()
    return js["items"][0]["id"] if isinstance(js,dict) and js.get("items") else None
MY_UID = get_uid(USER_NAME)

# ───── units (za ručni rad)
@st.cache_data(ttl=600)
def get_units():
    p={"svc":"core/search_items","params":json.dumps({
        "spec":{"itemsType":"avl_unit","propName":"sys_name",
                "propValueMask":"*","sortType":"sys_name"},
        "force":1,"flags":1,"from":0,"to":0}),"sid":SID}
    r=requests.post(API_PATH,data=p,timeout=15).json()
    lst=r["items"] if isinstance(r,dict) else r
    return [{"id":u["id"],"name":u.get("nm","Unknown"),
             "reg":u.get("prp",{}).get("reg_number","")} for u in lst]

def list_files(vid:int,target:date):
    p={"svc":"file/list","params":json.dumps({
        "itemId":vid,"storageType":2,"path":"tachograph/",
        "mask":"*","recursive":False,"fullPath":False}),"sid":SID}
    d=requests.post(API_PATH,data=p,timeout=15).json()
    if isinstance(d,dict) and d.get("error"):
        if d["error"]==4: return []
        st.error(f"Wialon error {d['error']}"); return []
    out=[]
    for f in d:
        ct=datetime.fromtimestamp(f.get("ct",0),UTC).date()
        mt=datetime.fromtimestamp(f.get("mt",0),UTC).date()
        if ct==target or mt==target:
            out.append(f); continue
        m=DATE_RE.search(f["n"])
        if m and datetime.strptime(m.group(),"%Y%m%d").date()==target:
            out.append(f)
    return sorted(out,key=lambda x:x.get("mt",x.get("ct",0)),reverse=True)

def fetch_file(vid:int,name:str):
    return requests.get(API_PATH,params={
        "svc":"file/get",
        "params":json.dumps({"itemId":vid,"storageType":2,"path":f"tachograph/{name}"}),
        "sid":SID},timeout=30).content

# ───── učitaj user-konfig
db       = load_db()
user_cfg = db.get(str(MY_UID), {"token":"","recipients":"","enabled":False})
# normalizuj recipients → string
user_cfg["recipients"] = ", ".join(user_cfg["recipients"]) if isinstance(user_cfg["recipients"], list) else str(user_cfg["recipients"] or "")

# ───── admin auth u session_state
if "admin_ok" not in st.session_state: st.session_state.admin_ok=False
if ADMIN_FLAG == ADMIN_PIN: st.session_state.admin_ok=True
# header zona (za admin)
header_left, admin_col = st.columns([3,1])

with admin_col:
    if not st.session_state.admin_ok:
        pin_in = st.text_input("Admin PIN", type="password", label_visibility="collapsed")
        if pin_in == ADMIN_PIN:
            st.session_state.admin_ok = True
            st.success("Admin ✅")
    else:
        st.markdown("### ⚙️ Admin")
        token = st.text_input("Token", value=user_cfg.get("token"), type="password")
        recip_val = st.text_area("Primaoci", value=user_cfg["recipients"], height=60)
        enabled = st.checkbox("Enabled", value=user_cfg.get("enabled", False))
        if st.button("💾 Snimi"):
            db[str(MY_UID)] = {
                "token": token.strip(),
                "recipients": recip_val.strip(),
                "enabled": enabled,
            }
            save_db(db)
            st.success("Snimljeno!")

# ───── sidebar (status + filter)
st.sidebar.success(f"▶️ {USER_NAME}")
st.sidebar.write(f"UserID: `{MY_UID}`")
st.sidebar.write("**Automatika:** " + ("✅ _uključena_" if user_cfg["enabled"] else "⏸️ _isključena_"))

units  = get_units()
search = st.sidebar.text_input("Pretraga vozila")
pick   = st.sidebar.date_input("Datum", date.today())

flt=[u for u in units if search.lower() in (u["reg"]+u["name"]).lower()]
if not flt: st.sidebar.info("Nema rezultata."); st.stop()
choice=st.sidebar.radio("Vozilo", flt, format_func=lambda v: f"{v['reg']} — {v['name']}")
vid=choice["id"]; files=list_files(vid,pick)

st.subheader(f"{choice['reg']} – {pick:%d.%m.%Y} ({len(files)})")
if not files: st.info("Nema fajlova."); st.stop()

if "checked" not in st.session_state: st.session_state.checked={}
cols=st.columns(3)
for i,f in enumerate(files):
    k=f"chk_{f['n']}"
    st.session_state.checked[k]=cols[i%3].checkbox(
        f["n"], st.session_state.checked.get(k,False), key=k)
sel=[f["n"] for f in files if st.session_state.checked.get(f"chk_{f['n']}")]

l,r=st.columns(2)
with l:
    st.markdown("### 📥 Download")
    if st.button("Preuzmi ZIP", disabled=not sel):
        mem=io.BytesIO()
        with zipfile.ZipFile(mem,"w") as zf:
            for fn in sel: zf.writestr(fn, fetch_file(vid,fn))
        st.download_button("Preuzmi", mem.getvalue(), "application/zip",
                           f"{choice['reg']}_{pick}.zip", use_container_width=True)

with r:
    st.markdown("### ✉️ Pošalji mail")
    if st.button("Pošalji", disabled=not(sel and user_cfg["recipients"])):
        buf=io.BytesIO()
        with zipfile.ZipFile(buf,"w") as zf:
            for fn in sel: zf.writestr(fn, fetch_file(vid,fn))
        msg=EmailMessage()
        msg["Subject"]=f"DDD {choice['reg']} {pick:%d-%m-%Y}"
        msg["From"]=SMTP_USER; msg["To"]=user_cfg["recipients"]
        msg.set_content("Export iz Streamlit aplikacije")
        msg.add_attachment(buf.getvalue(), maintype="application", subtype="zip",
                           filename=f"{choice['reg']}_{pick}.zip")
        with smtplib.SMTP(SMTP_SERVER,SMTP_PORT) as s:
            s.starttls(); s.login(SMTP_USER,SMTP_PASS); s.send_message(msg)
        st.success("Poslato!")
