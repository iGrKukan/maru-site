#!/usr/bin/env python3
"""
Сайт-витрина продукта Maru.
- Публичный маркетинговый лендинг на / (без логина).
- Закрытый раздел /materials (слайды, бриф, one-pager) — под паролем + защита от перебора.
- Форма заявки POST /api/lead → уходит в Telegram владельцу (через tg_send.py).
Только stdlib + вызов tg_send.py отдельным процессом. Слушает 127.0.0.1, наружу — через ngrok.
"""
import os, json, time, hmac, html, base64, hashlib, threading, subprocess
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse, unquote

BASE        = os.path.dirname(os.path.abspath(__file__))
PUBLIC_DIR  = os.path.join(BASE, "public")
HOST, PORT  = "127.0.0.1", 8200

USERS = { os.environ.get("SITE_USER", "demo"): os.environ.get("SITE_PASS", "Demo2026") }

# защита логина от перебора
MAX_FAILS, FAIL_WINDOW, LOCK_SECONDS, FAIL_DELAY = 5, 600, 900, 0.7
SESSION_TTL = 12 * 3600

# антиспам заявок
LEAD_MAX, LEAD_WINDOW = 5, 3600   # не более 5 заявок с IP за час
TG_PY  = os.path.expanduser("~/openclaw-server/venv/bin/python")
TG_TOOL = os.path.expanduser("~/openclaw-server/scripts/tools/tg_send.py")
LEAD_TO = "obiwan"

with open(os.path.join(BASE, ".secret")) as f:
    SECRET = f.read().strip().encode()

CTYPES = {".html":"text/html; charset=utf-8",".pdf":"application/pdf",".css":"text/css",
          ".js":"application/javascript",".png":"image/png",".jpg":"image/jpeg",
          ".jpeg":"image/jpeg",".svg":"image/svg+xml",".json":"application/json",
          ".ico":"image/x-icon",".webp":"image/webp"}

_attempts, _leads, _lock = {}, {}, threading.Lock()
def _now(): return time.time()

def record_fail(ip):
    with _lock:
        a=_attempts.setdefault(ip,{"fails":[],"locked_until":0}); now=_now()
        a["fails"]=[t for t in a["fails"] if now-t<FAIL_WINDOW]; a["fails"].append(now)
        if len(a["fails"])>=MAX_FAILS: a["locked_until"]=now+LOCK_SECONDS; a["fails"]=[]
def record_success(ip):
    with _lock: _attempts.pop(ip,None)
def lock_remaining(ip):
    with _lock:
        a=_attempts.get(ip)
        if not a: return 0
        r=a["locked_until"]-_now(); return int(r) if r>0 else 0
def lead_allowed(ip):
    with _lock:
        now=_now(); h=[t for t in _leads.get(ip,[]) if now-t<LEAD_WINDOW]
        _leads[ip]=h
        if len(h)>=LEAD_MAX: return False
        h.append(now); return True

def make_session(user):
    p=f"{user}|{int(_now())+SESSION_TTL}".encode()
    s=hmac.new(SECRET,p,hashlib.sha256).digest()
    return base64.urlsafe_b64encode(p+b"."+s).decode()
def valid_session(c):
    try:
        raw=base64.urlsafe_b64decode(c.encode()); p,s=raw.rsplit(b".",1)
        if not hmac.compare_digest(s,hmac.new(SECRET,p,hashlib.sha256).digest()): return False
        u,e=p.decode().split("|"); return u in USERS and int(e)>_now()
    except Exception: return False

LOGIN_PAGE = """<!DOCTYPE html><html lang=ru><head><meta charset=UTF-8>
<meta name=viewport content="width=device-width, initial-scale=1"><title>Вход — Maru</title><style>
*{{box-sizing:border-box;margin:0}}body{{font-family:-apple-system,"Segoe UI",Roboto,sans-serif;
background:#1f1e1d;color:#edebe6;min-height:100vh;display:flex;align-items:center;justify-content:center;padding:24px}}
.box{{background:#2b2a28;border:1px solid #3a3935;border-radius:20px;padding:38px 34px;width:100%;max-width:350px}}
.wm{{font-family:Georgia,'Times New Roman',serif;font-size:34px;font-weight:700;letter-spacing:.1em;color:#d97757;text-align:center;margin-bottom:6px}}
.sub{{text-align:center;color:#a8a59c;font-size:.9rem;margin-bottom:22px}}
label{{display:block;font-size:.85rem;color:#a8a59c;margin:12px 0 5px}}
input{{width:100%;padding:12px 14px;border-radius:12px;border:1px solid #3a3935;background:#1f1e1d;color:#edebe6;font-size:1rem}}
input:focus{{outline:none;border-color:#d97757}}
button{{width:100%;margin-top:22px;padding:13px;border:0;border-radius:14px;background:#d97757;color:#fff;font-weight:600;font-size:1rem;cursor:pointer}}
button:hover{{opacity:.9}}
.err{{background:rgba(217,119,87,.13);border:1px solid #5a3a2e;color:#e8a98c;border-radius:10px;padding:10px 12px;font-size:.9rem;margin-bottom:6px}}
a{{color:#a8a59c;font-size:.85rem}}.foot{{text-align:center;margin-top:16px}}
</style></head><body><form class=box method=post action=/login>
<div class=wm>Maru</div><div class=sub>Материалы для партнёров</div>
{err}
<label>Логин</label><input name=username autofocus autocomplete=username>
<label>Пароль</label><input name=password type=password autocomplete=current-password>
<button type=submit>Войти</button>
<div class=foot><a href="/">← на главную</a></div>
</form></body></html>"""

class H(BaseHTTPRequestHandler):
    server_version="maru/1.0"
    def log_message(self,*a): pass
    def client_ip(self):
        xff=self.headers.get("X-Forwarded-For","")
        return xff.split(",")[0].strip() if xff else self.client_address[0]
    def cookies(self):
        c={}
        for part in self.headers.get("Cookie","").split(";"):
            if "=" in part: k,v=part.strip().split("=",1); c[k]=v
        return c
    def is_authed(self): return valid_session(self.cookies().get("sess",""))
    def send_html(self,code,body,extra=None):
        d=body.encode(); self.send_response(code)
        self.send_header("Content-Type","text/html; charset=utf-8")
        self.send_header("Content-Length",str(len(d)))
        for k,v in (extra or []): self.send_header(k,v)
        self.end_headers(); self.wfile.write(d)
    def send_json(self,code,obj):
        d=json.dumps(obj,ensure_ascii=False).encode(); self.send_response(code)
        self.send_header("Content-Type","application/json; charset=utf-8")
        self.send_header("Content-Length",str(len(d))); self.end_headers(); self.wfile.write(d)
    def redirect(self,to,extra=None):
        self.send_response(303); self.send_header("Location",to)
        for k,v in (extra or []): self.send_header(k,v)
        self.end_headers()
    def login_page(self,code=200,err=""):
        block=f'<div class=err>{html.escape(err)}</div>' if err else ""
        self.send_html(code,LOGIN_PAGE.format(err=block))

    def do_GET(self):
        path=urlparse(self.path).path
        if path=="/healthz": return self.send_html(200,"ok")
        if path=="/login":
            return self.redirect("/materials/") if self.is_authed() else self.login_page()
        if path=="/logout":
            return self.redirect("/",[("Set-Cookie","sess=; Path=/; Max-Age=0")])
        if path=="/materials" : return self.redirect("/materials/")
        if path.startswith("/materials"):
            if not self.is_authed(): return self.redirect("/login")
        self.serve_static(path)

    def do_POST(self):
        path=urlparse(self.path).path
        if path=="/api/lead": return self.handle_lead()
        if path!="/login": return self.send_html(404,"not found")
        ip=self.client_ip(); rem=lock_remaining(ip)
        if rem>0:
            return self.login_page(429,f"Слишком много попыток. Повторите через {rem//60} мин {rem%60} с.")
        n=int(self.headers.get("Content-Length",0) or 0)
        form=parse_qs(self.rfile.read(n).decode("utf-8","replace"))
        u=form.get("username",[""])[0]; pw=form.get("password",[""])[0]
        if u in USERS and hmac.compare_digest(USERS[u],pw):
            record_success(ip)
            ck=f"sess={make_session(u)}; Path=/; Max-Age={SESSION_TTL}; HttpOnly; Secure; SameSite=Lax"
            return self.redirect("/materials/",[("Set-Cookie",ck)])
        record_fail(ip); time.sleep(FAIL_DELAY)
        left=MAX_FAILS-len(_attempts.get(ip,{}).get("fails",[])); rem2=lock_remaining(ip)
        if rem2>0: self.login_page(429,f"Слишком много попыток. Доступ заблокирован на {rem2//60} мин.")
        else: self.login_page(401,f"Неверный логин или пароль. Осталось попыток: {max(left,0)}.")

    def handle_lead(self):
        ip=self.client_ip()
        if not lead_allowed(ip):
            return self.send_json(429,{"ok":False,"error":"Слишком много заявок. Попробуйте позже."})
        n=int(self.headers.get("Content-Length",0) or 0)
        raw=self.rfile.read(n).decode("utf-8","replace")
        try: data=json.loads(raw) if "application/json" in self.headers.get("Content-Type","") else \
                  {k:v[0] for k,v in parse_qs(raw).items()}
        except Exception: data={}
        name=(data.get("name") or "").strip()[:120]
        contact=(data.get("contact") or "").strip()[:160]
        company=(data.get("company") or "").strip()[:160]
        msg=(data.get("message") or "").strip()[:1500]
        if len(name)<2 or len(contact)<3:
            return self.send_json(400,{"ok":False,"error":"Укажите имя и контакт."})
        text=("🟠 Заявка с сайта Maru\n\n"
              f"Имя: {name}\nКонтакт: {contact}\n"
              + (f"Компания: {company}\n" if company else "")
              + (f"\nСообщение:\n{msg}\n" if msg else "")
              + f"\n— IP {ip} · {time.strftime('%Y-%m-%d %H:%M')}")
        try:
            subprocess.run([TG_PY,TG_TOOL,"--to",LEAD_TO,"--force","--message",text],
                           timeout=30,capture_output=True)
        except Exception:
            pass  # заявку всё равно подтверждаем пользователю
        return self.send_json(200,{"ok":True})

    def serve_static(self,path):
        rel=unquote(path.lstrip("/")) or "index.html"
        full=os.path.normpath(os.path.join(PUBLIC_DIR,rel))
        if not full.startswith(PUBLIC_DIR+os.sep) and full!=PUBLIC_DIR:
            return self.send_html(403,"forbidden")
        if os.path.isdir(full): full=os.path.join(full,"index.html")
        if not os.path.isfile(full): return self.send_html(404,"Не найдено")
        ctype=CTYPES.get(os.path.splitext(full)[1].lower(),"application/octet-stream")
        try:
            with open(full,"rb") as f: d=f.read()
        except OSError: return self.send_html(404,"Не найдено")
        self.send_response(200); self.send_header("Content-Type",ctype)
        self.send_header("Content-Length",str(len(d)))
        if ctype.startswith("image/"): self.send_header("Cache-Control","public, max-age=86400")
        else: self.send_header("Cache-Control","no-cache")
        self.end_headers(); self.wfile.write(d)

if __name__=="__main__":
    print(f"serving {PUBLIC_DIR} on {HOST}:{PORT}",flush=True)
    ThreadingHTTPServer((HOST,PORT),H).serve_forever()
