# app.py – Wialon DDD Manager (SID-only, creatorId→fallback) – 2025-05-15
import io, json, zipfile, re, smtplib, base64, requests
from email.message import EmailMessage
from datetime import datetime, date
from urllib.parse import unquote
from pathlib import Path
from base64 import b64encode
from dateutil import tz
import streamlit as st

# ───────── META
st.set_page_config(page_title="Wialon DDD Manager", layout="wide")
UTC, DATE_RE = tz.tzutc(), re.compile(r"20\d{6}")
DATA_FILE = Path("users.json")

# ───────── URL (Wialon)
q          = st.query_params
SID        = q.get("sid")
BASE_URL   = unquote(q.get("baseUrl", "https://hst-api.wialon.com"))
USER_NAME  = q.get("user", "")
ADMIN_FLAG = q.get("admin")
API_PATH   = f"{BASE_URL.rstrip('/')}/wialon/ajax.html"
if not SID:
    st.stop("Pokreni iz Wialon-a – nedostaje sid=")

# ───────── secrets
SMTP_SERVER = st.secrets.get("SMTP_SERVER", "smtp.gmail.com")
SMTP_PORT   = int(st.secrets.get("SMTP_PORT", 587))
SMTP_USER   = st.secrets.get("SMTP_USER")
SMTP_PASS   = st.secrets.get("SMTP_PASS")
ADMIN_PIN   = st.secrets.get("ADMIN_PIN", "12345")
GITHUB_PAT  = st.secrets.get("GITHUB_PAT")
REPO        = st.secrets.get("GITHUB_REPO")
BRANCH      = "main"

# ───────── users.json helpers
def load_db(): return json.loads(DATA_FILE.read_text()) if DATA_FILE.exists() else {}
def push_to_github(txt:str):
    if not (GITHUB_PAT and REPO): return
    hdr={"Authorization":f"token {GITHUB_PAT}"}
    url=f"https://api.github.com/repos/{REPO}/contents/users.json"
    sha=None
    r0=requests.get(url,headers=hdr,params={"ref":BRANCH})
    if r0.status_code==200: sha=r0.json()["sha"]
    payload={"message":"update users.json via admin",
             "content":b64encode(txt.encode()).decode(),"branch":BRANCH}
    if sha: payload["sha"]=sha
    requests.put(url,headers=hdr,json=payload)
def save_db(db): DATA_FILE.write_text(json.dumps(db,indent=2)); push_to_github(json.dumps(db,indent=2))

# ───────── helper: userId
def get_uid(name:str)->int|None:
    p={"svc":"core/search_items","params":json.dumps({
        "spec":{"itemsType":"avl_user","propName":"sys_name",
                "propValueMask":name,"sortType":"sys_name"},
        "force":1,"flags":1,"from":0,"to":0}),"sid":SID}
    js=requests.post(API_PATH,data=p,timeout=8).json()
    return js["items"][0]["id"] if isinstance(js,dict) and js.get("items") else None
MY_UID=get_uid(USER_NAME)

# ───────── Units (creatorId → fallback)
@st.cache_data(ttl=600)
def get_units():
    def q(spec):
        r=requests.post(API_PATH,data={
            "svc":"core/search_items",
            "params":json.dumps({"spec":spec,"force":1,"flags":1,"from":0,"to":0}),
            "sid":SID},timeout=12).json()
        return r["items"] if isinstance(r,dict) else r
    it=q({"itemsType":"avl_unit","propName":"creatorId",
          "propValueMask":str(MY_UID),"sortType":"sys_name"})
    if not it:
        it=q({"itemsType":"avl_unit","propName":"sys_name",
              "propValueMask":"*","sortType":"sys_name"})
    return [{"id":u["id"],"name":u.get("nm","Unknown"),
             "reg":u.get("prp",{}).get("reg_number","")} for u in it]

def list_files(vid:int,target:date):
    p={"svc":"file/list","params":json.dumps({
        "itemId":vid,"storageType":2,"path":"tachograph/",
        "mask":"*","recursive":False,"fullPath":False}),"sid":SID}
    d=requests.post(API_PATH,data=p,timeout=12).json()
    if isinstance(d,dict) and d.get("error"): return []
    out=[]
    for f in d:
        ct=datetime.fromtimestamp(f.get("ct",0),UTC).date()
        mt=datetime.fromtimestamp(f.get("mt",0),UTC).date()
        if ct==target or mt==target: out.append(f); continue
        m=DATE_RE.search(f["n"])
        if m and datetime.strptime(m.group(),"%Y%m%d").date()==target: out.append(f)
    return sorted(out,key=lambda x:x.get("mt",x.get("ct",0)),reverse=True)

def fetch_file(vid:int,n:str):
    return requests.get(API_PATH,params={
        "svc":"file/get","params":json.dumps({
            "itemId":vid,"storageType":2,"path":f"tachograph/{n}"}),"sid":SID},
        timeout=30).content

# ───────── cfg
db=load_db()
user_cfg=db.get(str(MY_UID),{"token":"","recipients":"","enabled":False})
user_cfg["recipients"]=str(user_cfg.get("recipients") or "")

# ───────── admin auth
if "admin_ok" not in st.session_state: st.session_state.admin_ok=False
if ADMIN_FLAG==ADMIN_PIN: st.session_state.admin_ok=True

# ───────── header
_, admin = st.columns([3,1])
with admin:
    if not st.session_state.admin_ok:
        pin=st.text_input("Admin PIN", type="password", label_visibility="collapsed")
        if pin==ADMIN_PIN:
            st.session_state.admin_ok=True
            st.rerun()
    else:
        st.markdown("### ⚙️ Admin")
        token_val = st.text_input("Token", value=user_cfg["token"], type="password")
        recip_val = st.text_area(
            "Primaoci",
            value=str(user_cfg.get("recipients") or ""),
            height=60,
            key=f"admin_recips_{MY_UID}",
        )
        enabled = st.checkbox("Enabled", value=user_cfg["enabled"])
        if st.button("💾 Snimi"):
            db[str(MY_UID)]={"token":token_val.strip(),
                             "recipients":recip_val.strip(),
                             "enabled":enabled}
            save_db(db); st.success("Snimljeno!")

# ───────── sidebar
st.sidebar.success(f"▶️ {USER_NAME}")
st.sidebar.write(f"UserID: `{MY_UID}`")
st.sidebar.write("**Automatika:** "+
                 ("✅ _uključena_" if user_cfg["enabled"] else "⏸️ _isključena_"))
st.sidebar.markdown("**Primaoci:**")
st.sidebar.code(user_cfg["recipients"] or "—")

units=get_units()
if not units:
    st.sidebar.warning("Nijedno vozilo nije pronađeno."); st.stop()

search=st.sidebar.text_input("Pretraga")
pick  = st.sidebar.date_input("Datum", date.today())
flt=[u for u in units if search.lower() in (u["reg"]+u["name"]).lower()]
if not flt: st.sidebar.info("Nema rezultata."); st.stop()

choice=st.sidebar.radio("Vozilo",flt,format_func=lambda v:f"{v['reg']} — {v['name']}")
vid=choice["id"]; files=list_files(vid,pick)

st.subheader(f"{choice['reg']} – {pick:%d.%m.%Y} ({len(files)})")
if not files: st.info("Nema fajlova."); st.stop()

if "checked" not in st.session_state: st.session_state.checked={}
for i,f in enumerate(files):
    k=f"chk_{f['n']}"
    st.session_state.checked[k]=st.columns(3)[i%3].checkbox(
        f["n"], st.session_state.checked.get(k,False), key=k)
sel=[f["n"] for f in files if st.session_state.checked.get(f"chk_{f['n']}")]

l,r=st.columns(2)
with l:
    st.markdown("### 📥 Download")
    if st.button("Preuzmi ZIP",disabled=not sel):
        mem=io.BytesIO()
        with zipfile.ZipFile(mem,"w") as zf:
            for fn in sel:
                zf.writestr(fn, fetch_file(vid,fn))
        st.download_button("Preuzmi",mem.getvalue(),"application/zip",
                           f"{choice['reg']}_{pick}.zip",use_container_width=True)

with r:
    st.markdown("### ✉️ Pošalji mail")
    if st.button("Pošalji",disabled=not(sel and user_cfg["recipients"])):
        buf=io.BytesIO()
        with zipfile.ZipFile(buf,"w") as zf:
            for fn in sel:
                zf.writestr(fn, fetch_file(vid,fn))
        msg=EmailMessage()
        msg["Subject"]=f"DDD {choice['reg']} {pick:%d-%m-%Y}"
        msg["From"]=SMTP_USER; msg["To"]=user_cfg["recipients"]
        msg.set_content("Export iz Streamlit aplikacije")
        msg.add_attachment(buf.getvalue(),maintype="application",subtype="zip",
                           filename=f"{choice['reg']}_{pick}.zip")
        with smtplib.SMTP(SMTP_SERVER,SMTP_PORT) as s:
            s.starttls(); s.login(SMTP_USER,SMTP_PASS); s.send_message(msg)
        st.success("Poslato!")
