"""临时处置：重启设备使无障碍设置在开机时绑定。用完即删。"""

import json
import sqlite3
import urllib.request

con = sqlite3.connect("/opt/ntu-shower/server/data/ntu-shower.db")
tables = [r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'")]
t = [x for x in tables if "identit" in x][0]
row = con.execute(
    f"SELECT identity FROM {t} WHERE capabilities LIKE '%admin%' "
    "AND (device_scope LIKE '%\"*\"%' OR device_scope LIKE '%1c94310a%') LIMIT 1"
).fetchone()
ident = row[0]


def run(title, cmd, keep=600):
    req = urllib.request.Request(
        "http://127.0.0.1:8001/api/v1/devices/1c94310a536777d6/exec",
        data=json.dumps({"command": cmd}).encode(),
        headers={"Content-Type": "application/json", "X-Identity": ident},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=45) as r:
        body = json.loads(r.read().decode())
    out = (body.get("data") or {}).get("output") or json.dumps(body)[:200]
    print(f"===== {title} =====")
    print(out[:keep])


run("设置确认", "settings get secure enabled_accessibility_services && settings get secure accessibility_enabled")
run("重启设备", "reboot")
