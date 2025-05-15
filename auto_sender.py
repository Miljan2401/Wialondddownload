# auto_sender.py – headless skript
import os, io, zipfile, json, requests, smtplib
from email.message import EmailMessage
from datetime import datetime, timedelta, timezone

BASE = "https://hst-api.wialon.com/wialon/ajax.html"
TOKEN  = os.environ["TOKEN"]
SMTP_SRV = os.environ["SMTP_SERVER"]; SMTP_PORT = int(os.environ["SMTP_PORT"])
SMTP_USER = os.environ["SMTP_USER"];  SMTP_PASS = os.environ["SMTP_PASS"]
RECIPS = os.environ["RECIPIENTS"].split(",")

def login():
    r = requests.get(BASE, params={"svc":"token/login",
                                   "params":json.dumps({"token":TOKEN})})
    r.raise_for_status(); return r.json()["eid"]

def get_vehicles(sid):
    q = {"svc":"core/search_items",
         "params":json.dumps({"spec":{"itemsType":"avl_unit",
                                      "propName":"sys_name",
                                      "propValueMask":"*",
                                      "sortType":"sys_name"},
                              "force":1,"flags":1,"from":0,"to":0}),
         "sid":sid}
    return requests.post(BASE, data=q).json()["items"]

def list_files(sid, vid, limit=3):
    q = {"svc":"file/list",
         "params":json.dumps({"itemId":vid,"storageType":2,
                              "path":"tachograph/","recursive":False}),
         "sid":sid}
    files = requests.post(BASE, data=q).json()
    files.sort(key=lambda f:f.get("mt",f.get("ct",0)), reverse=True)
    return files[:limit]

def fetch(sid, vid, name):
    q={"svc":"file/get",
       "params":json.dumps({"itemId":vid,"storageType":2,
                            "path":f"tachograph/{name}"}),
       "sid":sid}
    return requests.get(BASE, params=q).content

def run():
    sid = login()
    for v in get_vehicles(sid):
        zmem = io.BytesIO()
        with zipfile.ZipFile(zmem,"w") as zf:
            for f in list_files(sid, v["id"]):
                zf.writestr(f["n"], fetch(sid, v["id"], f["n"]))
        if zmem.tell():
            msg = EmailMessage()
            msg["Subject"] = f"DDD auto {v.get('prp',{}).get('reg_number','')}"
            msg["From"] = SMTP_USER; msg["To"] = ", ".join(RECIPS)
            msg.set_content("Automatski export iz Wialona")
            msg.add_attachment(zmem.getvalue(), maintype="application",
                               subtype="zip", filename=f"{v['nm']}.zip")
            with smtplib.SMTP(SMTP_SRV, SMTP_PORT) as s:
                s.starttls(); s.login(SMTP_USER, SMTP_PASS); s.send_message(msg)

if __name__ == "__main__":
    run()
