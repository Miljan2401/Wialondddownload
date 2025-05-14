# app.py  –  Streamlit verzija “Wialon DDD Manager”

import os, io, zipfile, json, requests, smtplib, re
from email.message import EmailMessage
from datetime import datetime, date
from dateutil import tz
import streamlit as st
print("DEBUG – keys u st.secrets:", list(st.secrets.keys()), flush=True)
print("DEBUG – st.secrets content:", st.secrets.to_dict(), flush=True)


UTC = tz.tzutc()
DATE_RE = re.compile(r"20\d{6}")          # YYYYMMDD u imenu fajla
BASE_URL = "https://hst-api.wialon.com/wialon/ajax.html"

# ─── kredencijali iz .streamlit/secrets.toml ───────────────────
TOKEN   = st.secrets["TOKEN"]
MAILSRV = st.secrets["SMTP_SERVER"]
MAILPORT= int(st.secrets["SMTP_PORT"])
MAILUSER= st.secrets["SMTP_USER"]
MAILPASS= st.secrets["SMTP_PASS"]
RECIPS  = st.secrets["RECIPIENTS"]

# --------- Wialon helpers --------------------------------------
@st.cache_data(ttl=3600)          # cache 1 h
def login():
    r = requests.get(BASE_URL,
            params={"svc":"token/login",
                    "params":json.dumps({"token":TOKEN})})
    j = r.json()
    if "error" in j: st.error(j); st.stop()
    return j["eid"]

@st.cache_data(ttl=900)           # 15 min
def get_vehicles(session_id):
    r = requests.post(BASE_URL,
            data={"svc":"core/search_items",
                  "params":json.dumps({
                      "spec":{"itemsType":"avl_unit","propName":"sys_name",
                              "propValueMask":"*","sortType":"sys_name"},
                      "force":1,"flags":1,"from":0,"to":0}),
                  "sid":session_id})
    j = r.json()
    if "error" in j: st.error(j); st.stop()
    return [{"id":it["id"],
             "name":it.get("nm","Unknown"),
             "reg": it.get("prp",{}).get("reg_number","")} for it in j["items"]]

def list_files(session_id, vid, target:date):
    r = requests.post(BASE_URL,
            data={"svc":"file/list",
                  "params":json.dumps({"itemId":vid,"storageType":2,
                                       "path":"tachograph/","mask":"*",
                                       "recursive":False,"fullPath":False}),
                  "sid":session_id})
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

def fetch_file(session_id, vid, fname):
    p = {"svc":"file/get",
         "params":json.dumps({"itemId":vid,"storageType":2,
                              "path":f"tachograph/{fname}"}),
         "sid":session_id}
    return requests.get(BASE_URL, params=p).content

# ---------------------- Streamlit UI ---------------------------
st.set_page_config("Wialon DDD Manager", layout="wide")

sid = login()
vehicles = get_vehicles(sid)

col1,col2 = st.columns([1,3])

# ---- sidebar / filter ----------------------------------------
with col1:
    st.header("Vozila")
    search = st.text_input("Pretraga")
    filtered = [v for v in vehicles if search.lower() in (v["reg"]+v["name"]).lower()]
    vnames  = [f'{v["reg"]} — {v["name"]}' for v in filtered]
    choice  = st.radio("Izaberi vozilo", options=vnames if vnames else ["-"], index=0)

    st.markdown("---")
    pick_date = st.date_input("Datum", value=date.today())

# ---- fajlovi --------------------------------------------------
with col2:
    if vnames:
        vid = filtered[vnames.index(choice)]["id"]
        files = list_files(sid, vid, pick_date)
        st.subheader(f"Fajlovi ({len(files)})")
        if not files:
            st.info("Nema fajlova za taj datum.")
        else:
            sel = st.multiselect("Izaberi fajlove", options=[f["n"] for f in files])

            c1,c2 = st.columns(2)
            with c1:
                if st.button("⬇️ Preuzmi ZIP", disabled=not sel):
                    mem = io.BytesIO()
                    with zipfile.ZipFile(mem, "w") as zf:
                        for fn in sel:
                            zf.writestr(fn, fetch_file(sid, vid, fn))
                    st.download_button("Download ZIP",
                                       data=mem.getvalue(),
                                       file_name="ddd_files.zip",
                                       mime="application/zip")
            with c2:
                if st.button("📧 Pošalji email", disabled=not sel):
                    try:
                        buf = io.BytesIO()
                        with zipfile.ZipFile(buf,"w") as zf:
                            for fn in sel:
                                zf.writestr(fn, fetch_file(sid,vid,fn))
                        msg = EmailMessage()
                        msg["Subject"] = f"DDD fajlovi {pick_date}"
                        msg["From"] = MAILUSER
                        msg["To"]   = RECIPS
                        msg.set_content("Automatski eksport iz Streamlit app-a.")
                        msg.add_attachment(buf.getvalue(), maintype="application",
                                           subtype="zip", filename="ddd_files.zip")
                        with smtplib.SMTP(MAILSRV, MAILPORT) as s:
                            s.starttls(); s.login(MAILUSER, MAILPASS); s.send_message(msg)
                        st.success("Poslato!")
                    except Exception as e:
                        st.error(e)
