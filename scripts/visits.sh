#!/usr/bin/env bash
# Кто заходил на сайт: журнал Caddy с сервера → по одному посетителю (IP) на строку:
# первый и последний заход, сколько раз открыл страницу, браузер. Боты и служебные запросы отброшены.
# Запуск из WSL: scripts/visits.sh [дней, по умолчанию 14] [root@хост]
set -euo pipefail
DAYS=${1:-14}
HOST=${2:-root@135.106.211.80}
KEY=${SSH_KEY:-$HOME/.ssh/id_ed25519}

ssh -i "$KEY" -o BatchMode=yes "$HOST" "docker exec pcb-caddy sh -c 'cat /data/logs/access*.log 2>/dev/null'" |
python3 -c '
import json, re, sys, time
days = int(sys.argv[1]); since = time.time() - days * 86400
BOT = re.compile(r"bot|crawl|spider|curl|wget|python|go-http|scan|zgrab|masscan|httpx|headless|preview", re.I)
seen = {}
for line in sys.stdin:
    try: e = json.loads(line)
    except ValueError: continue
    r = e.get("request", {}); ts = e.get("ts", 0)
    if ts < since or r.get("method") != "GET" or e.get("status") != 200: continue
    if r.get("uri", "").split("?")[0] not in ("/", "/index.html"): continue
    ua = (r.get("headers", {}).get("User-Agent") or [""])[0]
    if not ua or BOT.search(ua): continue
    ip = r.get("client_ip") or r.get("remote_ip")
    v = seen.setdefault(ip, {"first": ts, "last": ts, "n": 0, "ua": ua, "host": r.get("host")})
    v["first"] = min(v["first"], ts); v["last"] = max(v["last"], ts); v["n"] += 1
def browser(ua):
    os_ = next((n for k, n in [("Android","Android"),("iPhone","iPhone"),("Windows","Windows"),("Mac OS","macOS"),("Linux","Linux")] if k in ua), "?")
    br = next((n for k, n in [("YaBrowser","Яндекс"),("Edg/","Edge"),("OPR/","Opera"),("Firefox","Firefox"),("Chrome","Chrome"),("Safari","Safari")] if k in ua), "?")
    return f"{br}, {os_}"
fmt = lambda t: time.strftime("%d.%m %H:%M", time.localtime(t))
if not seen: print(f"за {days} дн. заходов нет"); sys.exit()
print("%-40s %-12s %-12s %4s  браузер" % ("IP", "первый", "последний", "раз"))
for ip, v in sorted(seen.items(), key=lambda x: -x[1]["last"]):
    print("%-40s %-12s %-12s %4d  %s" % (ip, fmt(v["first"]), fmt(v["last"]), v["n"], browser(v["ua"])))
' "$DAYS"
