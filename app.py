#!/usr/bin/env python3
"""
Сайт-витрина продукта Maru.
- Публичный маркетинговый лендинг на / (без логина).
- Закрытый раздел /materials (слайды, бриф, one-pager) — под паролем + защита от перебора.
- Форма заявки POST /api/lead → уходит в Telegram владельцу (через tg_send.py).
Только stdlib + вызов tg_send.py отдельным процессом. Слушает 127.0.0.1, наружу — через ngrok.
"""
import os, json, time, hmac, html, base64, hashlib, threading, subprocess, queue, uuid, signal
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse, unquote

BASE        = os.path.dirname(os.path.abspath(__file__))
PUBLIC_DIR  = os.path.join(BASE, "public")
HOST, PORT  = "127.0.0.1", int(os.environ.get("PORT", 8200))

USERS = { os.environ.get("SITE_USER", "demo"): os.environ.get("SITE_PASS", "Demo2026") }

# --- admin-кабинет: секретный путь + отдельные креды (из .secret-admin, gitignored) ---
ADMIN = {}
_admin_file = os.path.join(BASE, ".secret-admin")
if os.path.exists(_admin_file):
    with open(_admin_file) as f:
        for line in f:
            if "=" in line and not line.lstrip().startswith("#"):
                k, v = line.strip().split("=", 1); ADMIN[k] = v
SLUG       = ADMIN.get("SLUG", "")          # секретный префикс пути; пусто => кабинет выключен
ADMIN_USER = ADMIN.get("ADMIN_USER", "admin")
ADMIN_PASS = ADMIN.get("ADMIN_PASS", "")

# защита логина от перебора
MAX_FAILS, FAIL_WINDOW, LOCK_SECONDS, FAIL_DELAY = 5, 600, 900, 0.7
# для admin строже: 3 неудачи => блок на 30 минут
ADMIN_MAX_FAILS, ADMIN_LOCK_SECONDS, ADMIN_FAIL_DELAY = 3, 1800, 1.0
SESSION_TTL       = 12 * 3600
ADMIN_SESSION_TTL = 2 * 3600                # admin-сессия живёт меньше

# --- агент-редактор (claude в sandbox правит ТОЛЬКО черновик ~/maru-editor/public) ---
HOME            = os.path.expanduser("~")
DRAFT_DIR       = os.path.join(HOME, "maru-editor")          # клон-черновик (git)
DRAFT_PUBLIC    = os.path.join(DRAFT_DIR, "public")          # рабочая папка агента
SANDBOX_PROFILE = os.path.join(BASE, "maru-edit.sb")
CLAUDE_BIN      = os.path.join(HOME, ".local/bin/claude")
EDITOR_CFG_DIR  = os.path.join(HOME, ".config/maru-editor-claude")
AGENT_MODEL     = os.environ.get("AGENT_MODEL", "sonnet")
AGENT_MAX_TURNS = 40
AGENT_TIMEOUT   = 300                        # сек., wall-clock киллер
TASK_MAX_LEN    = 4000
# Аутентификация — по ПОДПИСКЕ через CLI (НЕ API-ключ): long-lived OAuth-токен из `claude setup-token`.
# Под sandbox keychain/~.claude недоступны, поэтому токен передаём в env CLAUDE_CODE_OAUTH_TOKEN
# (имеет приоритет, работает с изолированным CLAUDE_CONFIG_DIR; --bare НЕ использовать).
_token_file = os.path.join(BASE, ".secret-oauth-token")
OAUTH_TOKEN = os.environ.get("CLAUDE_CODE_OAUTH_TOKEN", "")
if not OAUTH_TOKEN and os.path.exists(_token_file):
    with open(_token_file) as f: OAUTH_TOKEN = f.read().strip()

AGENT_SYSPROMPT = (
    "Ты — редактор-оформитель сайта-витрины Maru. Тебе разрешено менять ТОЛЬКО визуальную "
    "и текстовую составляющую сайта: HTML-разметку, CSS-стили, тексты, структуру секций в файлах "
    "текущей папки (это public/ сайта). СТРОГО ЗАПРЕЩЕНО: выходить за пределы текущей папки, "
    "трогать app.py, .git, любые секреты/ключи/данные, выполнять системные операции, читать что-либо "
    "вне сайта. Доступа к данным (1С, банк, боты, файлы пользователя) у тебя нет и быть не должно — "
    "если задача требует таких данных или действий вне правки сайта, вежливо откажись и объясни. "
    "Соблюдай фирменный стиль Maru: акцент терракотовый #d97757, фон #1f1e1d, поверхность #2b2a28, "
    "текст #edebe6, линии #3a3935, вордмарк «Maru» шрифтом Georgia serif. Правило двуязычности: "
    "русский текст — в DOM, английский — в JS-словаре EN по атрибутам data-i18n; добавляя текст, "
    "добавляй и перевод. Делай минимальные точечные правки под задачу, не ломай существующую вёрстку."
)

# антиспам заявок
LEAD_MAX, LEAD_WINDOW = 5, 3600   # не более 5 заявок с IP за час
LEAD_EMAIL = "sistemaov17@gmail.com"
EMAIL_PY   = os.path.expanduser("~/openclaw-server/venv/bin/python")
EMAIL_TOOL = os.path.expanduser("~/openclaw-server/scripts/tools/send_email.py")

with open(os.path.join(BASE, ".secret")) as f:
    SECRET = f.read().strip().encode()

CTYPES = {".html":"text/html; charset=utf-8",".pdf":"application/pdf",".css":"text/css",
          ".js":"application/javascript",".png":"image/png",".jpg":"image/jpeg",
          ".jpeg":"image/jpeg",".svg":"image/svg+xml",".json":"application/json",
          ".ico":"image/x-icon",".webp":"image/webp"}

_attempts, _admin_attempts, _leads, _lock = {}, {}, {}, threading.Lock()
def _now(): return time.time()

def _record_fail(store, ip, max_fails, lock_seconds):
    with _lock:
        a=store.setdefault(ip,{"fails":[],"locked_until":0}); now=_now()
        a["fails"]=[t for t in a["fails"] if now-t<FAIL_WINDOW]; a["fails"].append(now)
        if len(a["fails"])>=max_fails: a["locked_until"]=now+lock_seconds; a["fails"]=[]
def _lock_remaining(store, ip):
    with _lock:
        a=store.get(ip)
        if not a: return 0
        r=a["locked_until"]-_now(); return int(r) if r>0 else 0

def record_fail(ip):    _record_fail(_attempts, ip, MAX_FAILS, LOCK_SECONDS)
def record_success(ip):
    with _lock: _attempts.pop(ip,None)
def lock_remaining(ip): return _lock_remaining(_attempts, ip)

def admin_record_fail(ip):    _record_fail(_admin_attempts, ip, ADMIN_MAX_FAILS, ADMIN_LOCK_SECONDS)
def admin_record_success(ip):
    with _lock: _admin_attempts.pop(ip,None)
def admin_lock_remaining(ip): return _lock_remaining(_admin_attempts, ip)
def admin_fails_left(ip):
    with _lock: return max(ADMIN_MAX_FAILS-len(_admin_attempts.get(ip,{}).get("fails",[])),0)
def lead_allowed(ip):
    with _lock:
        now=_now(); h=[t for t in _leads.get(ip,[]) if now-t<LEAD_WINDOW]
        _leads[ip]=h
        if len(h)>=LEAD_MAX: return False
        h.append(now); return True

def make_session(user, role="materials", ttl=SESSION_TTL):
    p=f"{user}|{role}|{int(_now())+ttl}".encode()
    s=hmac.new(SECRET,p,hashlib.sha256).digest()
    return base64.urlsafe_b64encode(p+b"."+s).decode()
def session_role(c):
    """Возвращает роль ('materials'|'admin') если cookie валидна и не истекла, иначе None."""
    try:
        raw=base64.urlsafe_b64decode(c.encode()); p,s=raw.rsplit(b".",1)
        if not hmac.compare_digest(s,hmac.new(SECRET,p,hashlib.sha256).digest()): return None
        u,role,e=p.decode().split("|")
        if int(e)<=_now(): return None
        if role=="admin":     return "admin" if u==ADMIN_USER else None
        if role=="materials": return "materials" if u in USERS else None
        return None
    except Exception: return None
def valid_session(c):  # совместимость со старым кодом /materials
    return session_role(c) in ("materials","admin")

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

# --- admin: страница входа в кабинет (по секретному пути) ---
ADMIN_LOGIN_PAGE = """<!DOCTYPE html><html lang=ru><head><meta charset=UTF-8>
<meta name=viewport content="width=device-width, initial-scale=1"><title>Maru</title>
<meta name=robots content="noindex,nofollow"><style>
*{{box-sizing:border-box;margin:0}}body{{font-family:-apple-system,"Segoe UI",Roboto,sans-serif;
background:#1f1e1d;color:#edebe6;min-height:100vh;display:flex;align-items:center;justify-content:center;padding:24px}}
.box{{background:#2b2a28;border:1px solid #3a3935;border-radius:20px;padding:38px 34px;width:100%;max-width:350px}}
.wm{{font-family:Georgia,serif;font-size:34px;font-weight:700;letter-spacing:.1em;color:#d97757;text-align:center;margin-bottom:6px}}
.sub{{text-align:center;color:#a8a59c;font-size:.9rem;margin-bottom:22px}}
label{{display:block;font-size:.85rem;color:#a8a59c;margin:12px 0 5px}}
input{{width:100%;padding:12px 14px;border-radius:12px;border:1px solid #3a3935;background:#1f1e1d;color:#edebe6;font-size:1rem}}
input:focus{{outline:none;border-color:#d97757}}
button{{width:100%;margin-top:22px;padding:13px;border:0;border-radius:14px;background:#d97757;color:#fff;font-weight:600;font-size:1rem;cursor:pointer}}
.err{{background:rgba(217,119,87,.13);border:1px solid #5a3a2e;color:#e8a98c;border-radius:10px;padding:10px 12px;font-size:.9rem;margin-bottom:6px}}
</style></head><body><form class=box method=post action="{action}">
<div class=wm>Maru</div><div class=sub>Кабинет редактирования</div>
{err}
<label>Логин</label><input name=username autofocus autocomplete=off>
<label>Пароль</label><input name=password type=password autocomplete=off>
<button type=submit>Войти</button>
</form></body></html>"""

# --- admin: полноценный UI кабинета (токен __SLUG__ заменяется при отдаче) ---
ADMIN_CABINET = r"""<!DOCTYPE html><html lang=ru><head><meta charset=UTF-8>
<meta name=viewport content="width=device-width, initial-scale=1"><title>Кабинет — Maru</title>
<meta name=robots content="noindex,nofollow"><style>
*{box-sizing:border-box;margin:0}
:root{--bg:#1f1e1d;--surf:#2b2a28;--line:#3a3935;--txt:#edebe6;--mut:#a8a59c;--acc:#d97757}
html,body{height:100%}
body{font-family:-apple-system,"Segoe UI",Roboto,sans-serif;background:var(--bg);color:var(--txt);display:flex;flex-direction:column}
header{display:flex;align-items:center;gap:14px;padding:12px 20px;border-bottom:1px solid var(--line);flex:0 0 auto}
.wm{font-family:Georgia,serif;font-size:22px;font-weight:700;letter-spacing:.08em;color:var(--acc)}
.tag{color:var(--mut);font-size:.9rem}
header .sp{flex:1}
header a{color:var(--mut);font-size:.85rem;text-decoration:none}
header a:hover{color:var(--acc)}
.dot{width:9px;height:9px;border-radius:50%;background:#4a7;display:inline-block;margin-right:6px}
.dot.busy{background:var(--acc);animation:pulse 1s infinite}
@keyframes pulse{50%{opacity:.3}}
main{flex:1;display:grid;grid-template-columns:minmax(360px,1fr) minmax(420px,1.2fr);gap:0;min-height:0}
.col{display:flex;flex-direction:column;min-height:0}
.col.left{border-right:1px solid var(--line)}
.pane{padding:16px 18px}
.pane.grow{flex:1;min-height:0;display:flex;flex-direction:column}
h3{font-size:.8rem;text-transform:uppercase;letter-spacing:.05em;color:var(--mut);margin-bottom:10px;font-weight:600}
textarea{width:100%;height:110px;resize:vertical;padding:12px 14px;border-radius:12px;border:1px solid var(--line);background:#1a1918;color:var(--txt);font-size:.98rem;font-family:inherit}
textarea:focus{outline:none;border-color:var(--acc)}
.row{display:flex;gap:10px;margin-top:10px;align-items:center;flex-wrap:wrap}
button{padding:10px 16px;border:0;border-radius:11px;font-weight:600;font-size:.92rem;cursor:pointer;background:var(--acc);color:#fff}
button.ghost{background:transparent;border:1px solid var(--line);color:var(--txt)}
button:disabled{opacity:.45;cursor:not-allowed}
button:hover:not(:disabled){opacity:.9}
#log{flex:1;overflow:auto;background:#1a1918;border:1px solid var(--line);border-radius:12px;padding:12px;font-size:.9rem;line-height:1.5;white-space:pre-wrap;word-break:break-word}
#log .l{padding:3px 0;border-bottom:1px solid rgba(255,255,255,.04)}
#log .l.err{color:#e8a98c}#log .l.ok{color:#9cd6a0}
.diff{font-family:ui-monospace,Menlo,monospace;font-size:.82rem;color:var(--mut);white-space:pre-wrap;background:#1a1918;border:1px solid var(--line);border-radius:10px;padding:10px;max-height:120px;overflow:auto}
.frame{flex:1;min-height:0;border:1px solid var(--line);border-radius:12px;overflow:hidden;background:#fff}
iframe{width:100%;height:100%;border:0;display:block}
.toolbar{display:flex;gap:8px;align-items:center;margin-bottom:10px;flex-wrap:wrap}
.toolbar .sp{flex:1}
input.msg{flex:1;min-width:140px;padding:9px 12px;border-radius:10px;border:1px solid var(--line);background:#1a1918;color:var(--txt);font-size:.9rem}
.note{color:var(--mut);font-size:.8rem;margin-top:8px}
.toast{position:fixed;bottom:18px;left:50%;transform:translateX(-50%);background:var(--surf);border:1px solid var(--line);border-radius:12px;padding:12px 18px;font-size:.9rem;opacity:0;transition:.3s;pointer-events:none;max-width:90%}
.toast.show{opacity:1}
</style></head><body>
<header>
  <span class=wm>Maru</span><span class=tag>Кабинет редактирования</span>
  <span class=sp></span>
  <span id=status><span class="dot" id=dot></span><span id=statustext>готов</span></span>
  <a href="/__SLUG__/logout">Выйти</a>
</header>
<main>
  <section class="col left">
    <div class=pane>
      <h3>Задача агенту</h3>
      <textarea id=task placeholder="Например: сделай заголовок на главном экране крупнее и поменяй текст кнопки на «Получить демо»"></textarea>
      <div class=row>
        <button id=send>Отправить агенту</button>
        <button id=stop class=ghost disabled>Остановить</button>
      </div>
      <div class=note>Агент правит только визуал и тексты в черновике. Изменения попадут на сайт после «Опубликовать».</div>
    </div>
    <div class="pane grow">
      <h3>Ход выполнения</h3>
      <div id=log></div>
    </div>
  </section>
  <section class=col>
    <div class="pane grow">
      <div class=toolbar>
        <h3 style="margin:0">Превью черновика</h3>
        <span class=sp></span>
        <button class=ghost id=refresh>Обновить</button>
      </div>
      <div class=frame><iframe id=preview src="/__SLUG__/preview/"></iframe></div>
    </div>
    <div class=pane>
      <h3>Изменения и публикация</h3>
      <div class=diff id=diff>загрузка…</div>
      <div class=toolbar style="margin-top:10px">
        <input class=msg id=msg placeholder="комментарий к публикации (необязательно)">
        <button id=publish>Опубликовать</button>
        <button class=ghost id=discard>Откатить черновик</button>
      </div>
    </div>
  </section>
</main>
<div class=toast id=toast></div>
<script>
const SLUG="__SLUG__", API="/"+SLUG;
const $=id=>document.getElementById(id);
let busy=false, es=null;
function toast(m){const t=$("toast");t.textContent=m;t.classList.add("show");setTimeout(()=>t.classList.remove("show"),3200);}
function setBusy(b){busy=b;$("send").disabled=b;$("stop").disabled=!b;$("publish").disabled=b;$("discard").disabled=b;
  $("dot").className="dot"+(b?" busy":"");$("statustext").textContent=b?"агент работает…":"готов";}
function logLine(msg){const d=document.createElement("div");d.className="l"+(msg.startsWith("⛔")?" err":msg.startsWith("✅")?" ok":"");
  d.textContent=msg;$("log").appendChild(d);$("log").scrollTop=$("log").scrollHeight;}
function refreshPreview(){$("preview").src=API+"/preview/?t="+Date.now();}
async function loadDiff(){try{const r=await fetch(API+"/api/diff");const j=await r.json();
  $("diff").textContent=j.dirty?(j.stat||j.names):"черновик совпадает с опубликованной версией — менять нечего";}catch(e){$("diff").textContent="(не удалось загрузить)";}}
async function send(){const task=$("task").value.trim();if(task.length<3){toast("Опишите задачу");return;}
  setBusy(true);$("log").innerHTML="";logLine("⏳ отправляю задачу…");
  let j;try{const r=await fetch(API+"/api/task",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({task})});j=await r.json();}
  catch(e){logLine("⛔ сеть: "+e);setBusy(false);return;}
  if(!j.ok){logLine("⛔ "+(j.error||"ошибка"));setBusy(false);return;}
  es=new EventSource(API+"/api/stream/"+j.run_id);
  es.onmessage=ev=>{const d=JSON.parse(ev.data);
    if(d.kind==="log")logLine(d.msg);
    if(d.kind==="end"){es.close();es=null;setBusy(false);refreshPreview();loadDiff();
      logLine(d.status==="done"?"— завершено —":"— остановлено с ошибкой —");}};
  es.onerror=()=>{if(es){es.close();es=null;}setBusy(false);};}
async function publish(){if(!confirm("Опубликовать черновик на живой сайт?"))return;setBusy(true);
  try{const r=await fetch(API+"/api/publish",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({message:$("msg").value})});
    const j=await r.json();toast(j.ok?"Опубликовано ✓":"Не вышло: "+j.msg);}catch(e){toast("Ошибка: "+e);}
  setBusy(false);loadDiff();}
async function discard(){if(!confirm("Сбросить все непубликованные правки черновика?"))return;setBusy(true);
  try{const r=await fetch(API+"/api/discard",{method:"POST"});const j=await r.json();toast(j.msg||"");}catch(e){toast("Ошибка: "+e);}
  setBusy(false);refreshPreview();loadDiff();}
$("send").onclick=send;
$("stop").onclick=async()=>{await fetch(API+"/api/stop",{method:"POST"});toast("Остановка…");};
$("refresh").onclick=refreshPreview;
$("publish").onclick=publish;
$("discard").onclick=discard;
$("task").addEventListener("keydown",e=>{if((e.metaKey||e.ctrlKey)&&e.key==="Enter")send();});
loadDiff();
</script></body></html>"""

# ================== агент: реестр запусков + спавн в sandbox ==================
# Один прогон одновременно (_run_lock). Каждый прогон = subprocess claude в sandbox-exec,
# reader-поток парсит stream-json и кладёт человекочитаемые события в очередь, откуда их
# забирает SSE-эндпоинт. Все правки идут ТОЛЬКО в DRAFT_PUBLIC (барьер — sandbox-профиль).
_run_lock = threading.Lock()
RUNS = {}                       # run_id -> {"q":Queue,"proc":Popen,"status":str,"task":str,"started":float}
AUDIT_LOG = os.path.join(BASE, "admin-audit.log")

def _audit(msg):
    try:
        with open(AUDIT_LOG,"a") as f: f.write(f"{time.strftime('%F %T')} {msg}\n")
    except Exception: pass

def agent_busy():
    return any(r["status"]=="running" for r in RUNS.values())

def _humanize(ev):
    """stream-json событие claude -> короткая строка для пользователя (или None, чтобы пропустить)."""
    t=ev.get("type")
    if t=="system" and ev.get("subtype")=="init":
        return "▶ агент запущен"
    if t=="assistant":
        for b in ev.get("message",{}).get("content",[]):
            if b.get("type")=="text" and b.get("text","").strip():
                return "💬 "+b["text"].strip()
            if b.get("type")=="tool_use":
                name=b.get("name"); inp=b.get("input",{})
                fp=inp.get("file_path") or inp.get("path") or ""
                fp=fp.replace(DRAFT_PUBLIC+"/","").replace(DRAFT_PUBLIC,"")
                if name in ("Edit","Write"):   return f"✏️ правит {fp or 'файл'}"
                if name=="Read":               return f"👁 читает {fp or 'файл'}"
                if name in ("Glob","Grep"):    return f"🔎 ищет: {inp.get('pattern','')}"
                return f"🔧 {name}"
    if t=="result":
        if ev.get("is_error"):
            return "⛔ ошибка: "+str(ev.get("result") or ev.get("api_error_status") or "неизвестно")
        cost=ev.get("total_cost_usd"); c=f" (~${cost:.3f})" if isinstance(cost,(int,float)) else ""
        return f"✅ готово{c}"
    return None

def _reader(run_id, proc):
    """Читает stdout claude построчно, парсит JSON, кладёт события в очередь."""
    r=RUNS[run_id]; q=r["q"]
    try:
        for line in iter(proc.stdout.readline, b""):
            line=line.decode("utf-8","replace").strip()
            if not line: continue
            try: ev=json.loads(line)
            except Exception: continue
            msg=_humanize(ev)
            if msg: q.put({"kind":"log","msg":msg})
            if ev.get("type")=="result":
                r["result"]=ev
    finally:
        proc.stdout.close() if proc.stdout else None
        code=proc.wait()
        r["status"]="error" if (code!=0 or (r.get("result") or {}).get("is_error")) else "done"
        res=r.get("result") or {}
        _audit(f"run {run_id} {r['status']} turns={res.get('num_turns')} cost={res.get('total_cost_usd')} task={r['task'][:200]!r}")
        q.put({"kind":"end","status":r["status"]})

def _timeout_killer(run_id, proc):
    t0=time.time()
    while proc.poll() is None:
        if time.time()-t0>AGENT_TIMEOUT:
            try: proc.send_signal(signal.SIGTERM); time.sleep(2); proc.kill()
            except Exception: pass
            RUNS[run_id]["q"].put({"kind":"log","msg":f"⏱ превышен лимит {AGENT_TIMEOUT}s — остановлено"})
            return
        time.sleep(1)

def start_agent(task):
    """Запускает claude в sandbox. Возвращает (run_id, error)."""
    if not OAUTH_TOKEN:
        return None, "Токен подписки не настроен (.secret-oauth-token — см. `claude setup-token`)."
    if not os.path.isdir(DRAFT_PUBLIC):
        return None, "Черновик ~/maru-editor/public не найден."
    with _run_lock:
        if agent_busy(): return None, "Агент уже выполняет задачу — дождитесь завершения."
        run_id=uuid.uuid4().hex[:12]
        cmd=["/usr/bin/sandbox-exec","-f",SANDBOX_PROFILE,
             CLAUDE_BIN,"-p",task,
             "--model",AGENT_MODEL,
             "--tools","Read,Edit,Write,Glob,Grep",
             "--permission-mode","acceptEdits",
             "--output-format","stream-json","--verbose",
             "--no-session-persistence","--disable-slash-commands",
             "--strict-mcp-config","--mcp-config",'{"mcpServers":{}}',
             "--max-turns",str(AGENT_MAX_TURNS),
             "--add-dir",DRAFT_PUBLIC,
             "--append-system-prompt",AGENT_SYSPROMPT]
        env={"PATH":"/opt/homebrew/bin:/usr/bin:/bin",
             "HOME":HOME,
             "CLAUDE_CONFIG_DIR":EDITOR_CFG_DIR,
             "CLAUDE_CODE_OAUTH_TOKEN":OAUTH_TOKEN}
        proc=subprocess.Popen(cmd,cwd=DRAFT_PUBLIC,env=env,
                              stdout=subprocess.PIPE,stderr=subprocess.DEVNULL)
        RUNS[run_id]={"q":queue.Queue(),"proc":proc,"status":"running","task":task,"started":time.time()}
        threading.Thread(target=_reader,args=(run_id,proc),daemon=True).start()
        threading.Thread(target=_timeout_killer,args=(run_id,proc),daemon=True).start()
        _audit(f"run {run_id} start task={task[:200]!r}")
        return run_id, None

def stop_agent():
    for rid,r in RUNS.items():
        if r["status"]=="running":
            try: r["proc"].send_signal(signal.SIGTERM); time.sleep(1); r["proc"].kill()
            except Exception: pass
            return True
    return False

# --- git-операции над черновиком (выполняет бэкенд вне sandbox; правки агента уже в DRAFT_PUBLIC) ---
def _git(*args, timeout=60):
    p=subprocess.run(["git","-C",DRAFT_DIR,*args],capture_output=True,text=True,timeout=timeout)
    return p.returncode, p.stdout, p.stderr

def draft_diff():
    """Сводка незакоммиченных правок черновика (что изменится при публикации)."""
    _git("add","-A","-N")                          # intent-to-add: показать и новые файлы
    _,stat,_ = _git("diff","--stat","--","public")
    _,names,_= _git("diff","--name-status","--","public")
    return {"stat":stat.strip(),"names":names.strip(),"dirty":bool(names.strip())}

def publish_draft(message):
    if agent_busy(): return False,"Агент ещё работает — дождитесь завершения."
    rc,_,_=_git("add","-A","--","public")
    rc,out,err=_git("diff","--cached","--quiet","--","public")
    if rc==0: return False,"Нет изменений для публикации."
    msg=("cabinet: "+(message or "обновление содержимого сайта"))[:200]
    rc,o,e=_git("commit","-m",msg,"--","public")
    if rc!=0: return False,f"commit: {e or o}"
    rc,o,e=_git("push","origin","main",timeout=120)
    if rc!=0:
        _git("reset","--soft","HEAD~1")            # откат коммита, чтобы не копить локальные
        return False,f"push: {e or o}"
    _audit(f"publish '{msg}' pushed")
    # мгновенно подтянуть на живой сайт
    try: subprocess.run(["bash",os.path.join(BASE,"deploy.sh")],timeout=120,capture_output=True)
    except Exception: pass
    return True,msg

def discard_draft():
    if agent_busy(): return False,"Агент ещё работает — дождитесь завершения."
    _git("fetch","origin","main",timeout=60)
    _git("reset","--hard","origin/main")
    _git("clean","-fd","--","public")
    _audit("draft discarded (reset to origin/main)")
    return True,"Черновик сброшен к опубликованной версии."

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
    def is_admin(self):  return session_role(self.cookies().get("asess","")) == "admin"
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
    def admin_login_page(self,code=200,err=""):
        block=f'<div class=err>{html.escape(err)}</div>' if err else ""
        self.send_html(code,ADMIN_LOGIN_PAGE.format(err=block,action=f"/{SLUG}/login"))

    # секретный путь активен только если SLUG задан в .secret-admin
    def is_admin_path(self,path):
        return bool(SLUG) and (path==f"/{SLUG}" or path.startswith(f"/{SLUG}/"))

    def do_GET(self):
        path=urlparse(self.path).path
        if path=="/healthz": return self.send_html(200,"ok")
        if self.is_admin_path(path): return self.admin_get(path)
        if path=="/login":
            return self.redirect("/materials/") if self.is_authed() else self.login_page()
        if path=="/logout":
            return self.redirect("/",[("Set-Cookie","sess=; Path=/; Max-Age=0")])
        if path=="/materials" : return self.redirect("/materials/")
        if path.startswith("/materials"):
            if not self.is_authed(): return self.redirect("/login")
        self.serve_static(path)

    # --- admin-кабинет (всё под секретным префиксом /<SLUG>/) ---
    def admin_get(self,path):
        sub=path[len(f"/{SLUG}"):]                 # часть после слага: ""|"/"|"/logout"|...
        if sub in ("","/"):
            if not self.is_admin(): return self.admin_login_page()
            return self.send_html(200,ADMIN_CABINET.replace("__SLUG__",SLUG))
        if sub=="/logout":
            return self.redirect(f"/{SLUG}/",[("Set-Cookie",f"asess=; Path=/{SLUG}; Max-Age=0")])
        # прочие admin-пути требуют входа; без него — обычный 404 (не выдаём существование)
        if not self.is_admin(): return self.send_html(404,"Не найдено")
        if sub.startswith("/api/stream/"):
            return self.sse_stream(sub.rsplit("/",1)[-1])
        if sub=="/api/diff":
            return self.send_json(200,{"ok":True,**draft_diff()})
        if sub=="/api/status":
            return self.send_json(200,{"ok":True,"busy":agent_busy()})
        if sub=="/preview" or sub.startswith("/preview/"):
            return self.serve_draft(sub[len("/preview"):])
        return self.send_html(404,"Не найдено")

    def serve_draft(self,relpath):
        rel=unquote(relpath.lstrip("/")) or "index.html"
        full=os.path.normpath(os.path.join(DRAFT_PUBLIC,rel))
        if not full.startswith(DRAFT_PUBLIC+os.sep) and full!=DRAFT_PUBLIC:
            return self.send_html(403,"forbidden")
        if os.path.isdir(full): full=os.path.join(full,"index.html")
        if not os.path.isfile(full): return self.send_html(404,"Не найдено")
        ctype=CTYPES.get(os.path.splitext(full)[1].lower(),"application/octet-stream")
        try:
            with open(full,"rb") as f: d=f.read()
        except OSError: return self.send_html(404,"Не найдено")
        self.send_response(200); self.send_header("Content-Type",ctype)
        self.send_header("Content-Length",str(len(d)))
        self.send_header("Cache-Control","no-store")     # превью всегда свежее
        self.end_headers(); self.wfile.write(d)

    def sse_stream(self, run_id):
        r=RUNS.get(run_id)
        if not r: return self.send_html(404,"нет такого запуска")
        self.send_response(200)
        self.send_header("Content-Type","text/event-stream; charset=utf-8")
        self.send_header("Cache-Control","no-cache")
        self.send_header("Connection","keep-alive")
        self.end_headers()
        q=r["q"]
        try:
            while True:
                try: ev=q.get(timeout=20)
                except queue.Empty:
                    self.wfile.write(b": keep-alive\n\n"); self.wfile.flush(); continue
                self.wfile.write(f"data: {json.dumps(ev,ensure_ascii=False)}\n\n".encode()); self.wfile.flush()
                if ev.get("kind")=="end": break
        except (BrokenPipeError,ConnectionResetError):
            pass

    def admin_login(self):
        ip=self.client_ip(); rem=admin_lock_remaining(ip)
        if rem>0:
            return self.admin_login_page(429,f"Слишком много попыток. Доступ заблокирован на {rem//60} мин.")
        n=int(self.headers.get("Content-Length",0) or 0)
        form=parse_qs(self.rfile.read(n).decode("utf-8","replace"))
        u=form.get("username",[""])[0]; pw=form.get("password",[""])[0]
        ok = bool(ADMIN_PASS) and u==ADMIN_USER and hmac.compare_digest(ADMIN_PASS,pw)
        if ok:
            admin_record_success(ip)
            ck=(f"asess={make_session(u,'admin',ADMIN_SESSION_TTL)}; Path=/{SLUG}; "
                f"Max-Age={ADMIN_SESSION_TTL}; HttpOnly; Secure; SameSite=Strict")
            return self.redirect(f"/{SLUG}/",[("Set-Cookie",ck)])
        admin_record_fail(ip); time.sleep(ADMIN_FAIL_DELAY)
        rem2=admin_lock_remaining(ip)
        if rem2>0: self.admin_login_page(429,f"Слишком много попыток. Доступ заблокирован на {rem2//60} мин.")
        else:      self.admin_login_page(401,f"Неверный логин или пароль. Осталось попыток: {admin_fails_left(ip)}.")

    def admin_publish(self):
        n=int(self.headers.get("Content-Length",0) or 0)
        raw=self.rfile.read(n).decode("utf-8","replace") if n else ""
        try: data=json.loads(raw) if raw else {}
        except Exception: data={}
        ok,m=publish_draft((data.get("message") or "").strip()[:200])
        return self.send_json(200 if ok else 409,{"ok":ok,"msg":m})

    def admin_task(self):
        n=int(self.headers.get("Content-Length",0) or 0)
        raw=self.rfile.read(n).decode("utf-8","replace")
        try: data=json.loads(raw)
        except Exception: data={}
        task=(data.get("task") or "").strip()
        if len(task)<3:           return self.send_json(400,{"ok":False,"error":"Опишите задачу."})
        if len(task)>TASK_MAX_LEN: return self.send_json(400,{"ok":False,"error":f"Слишком длинно (>{TASK_MAX_LEN})."})
        run_id,err=start_agent(task)
        if err: return self.send_json(409,{"ok":False,"error":err})
        return self.send_json(200,{"ok":True,"run_id":run_id})

    def do_POST(self):
        path=urlparse(self.path).path
        if self.is_admin_path(path):
            if path==f"/{SLUG}/login": return self.admin_login()
            if not self.is_admin(): return self.send_html(404,"Не найдено")
            if path==f"/{SLUG}/api/task": return self.admin_task()
            if path==f"/{SLUG}/api/stop": return self.send_json(200,{"ok":stop_agent()})
            if path==f"/{SLUG}/api/publish": return self.admin_publish()
            if path==f"/{SLUG}/api/discard":
                ok,m=discard_draft(); return self.send_json(200 if ok else 409,{"ok":ok,"msg":m})
            return self.send_html(404,"Не найдено")
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
        subject = f"Заявка с сайта Maru — {name}"
        text=("Новая заявка с сайта Maru\n\n"
              f"Имя: {name}\nКонтакт: {contact}\n"
              + (f"Компания: {company}\n" if company else "")
              + (f"\nСообщение:\n{msg}\n" if msg else "")
              + f"\n— IP {ip} · {time.strftime('%Y-%m-%d %H:%M')}")
        try:
            subprocess.run([EMAIL_PY,EMAIL_TOOL,"--to",LEAD_EMAIL,
                            "--subject",subject,"--body",text],
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
