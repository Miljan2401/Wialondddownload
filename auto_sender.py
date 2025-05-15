import os, io, zipfile, json, requests, smtplib, re
from email.message import EmailMessage
from datetime import datetime, date, timedelta, timezone
from dateutil import tz

UTC = timezone.utc
DATE_RE = re.compile(r"20\\d{6}")
TARGET = date.today() - timedelta(days=1)   # jučerašnji dan

def files_for_day(token, base, vid):
    api = f"{base.rstrip('/')}/wialon/ajax.html"
    sid = requests.get(api, params={"svc":"token/login","params":json.dumps({"token":token})}).json()["eid"]
    q = {"svc":"file/list","params":json.dumps({"itemId":vid,"storageType":2,
          "path":"tachograph/","mask":"*","recursive":False,"fullPath":False}),"sid":sid}
    data=requests.post(api,data=q).json()
    out=[]
    for f in data:
        ct=datetime.fromtimestamp(f.get("ct",0),UTC).date()
        mt=datetime.fromtimestamp(f.get("mt",0),UTC).date()
        if ct==TARGET or mt==TARGET: out.append(f); continue
        m=DATE_RE.search(f["n"])
        if m and datetime.strptime(m.group(),"%Y%m%d").date()==TARGET: out.append(f)
    return out, sid

def run():
    BASE     = os.getenv("BASE_URL","https://hst-api.wialon.com")
    TOKEN    = os.environ["TOKEN"]
    RECIPS   = os.environ["RECIPIENTS"]
    SMTP_SRV = os.environ["SMTP_SERVER"]; SMTP_PORT = int(os.environ["SMTP_PORT"])
    SMTP_USR = os.environ["SMTP_USER"];   SMTP_PSW = os.environ["SMTP_PASS"]

    api      = f"{BASE.rstrip('/')}/wialon/ajax.html"
    sid      = requests.get(api, params={"svc":"token/login","params":json.dumps({"token":TOKEN})}).json()["eid"]
    units    = requests.post(api,data={"svc":"core/search_items","params":json.dumps({
                     "spec":{"itemsType":"avl_unit","propName":"sys_name",
                             "propValueMask":"*","sortType":"sys_name"},
                     "force":1,"flags":1,"from":0,"to":0}),"sid":sid}).json()["items"]

    for u in units:
        files,_=files_for_day(TOKEN,BASE,u["id"])
        if not files: continue
        buf=io.BytesIO(); zipfile.ZipFile(buf,"w").close()
        with zipfile.ZipFile(buf,"a") as zf:
            for f in files:
                cont=requests.get(api, params={"svc":"file/get","params":json.dumps({
                     "itemId":u["id"],"storageType":2,"path":f"tachograph/{f['n']}" }),"sid":sid}).content
                zf.writestr(f["n"], cont)
        msg=EmailMessage(); msg["Subject"]=f"DDD {u.get('prp',{}).get('reg_number','')} {TARGET}"
        msg["From"]=SMTP_USR; msg["To"]=RECIPS
        msg.set_content("Automatski DDD export")
        msg.add_attachment(buf.getvalue(),maintype="application",subtype="zip",
                           filename=f"{u['nm']}_{TARGET}.zip")
        with smtplib.SMTP(SMTP_SRV, SMTP_PORT) as s:
            s.starttls(); s.login(SMTP_USR, SMTP_PSW); s.send_message(msg)

if __name__=="__main__": run()
