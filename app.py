"""Rummi en línea: salas con link para compartir (2-4 jugadores, bots opcionales).

El estado vive en memoria: usar UN solo worker de gunicorn (ver render.yaml).
Los clientes consultan /api/state cada ~1.5 s para ver los cambios.
"""
import os
import random
import threading
import uuid
from functools import wraps

from flask import Flask, jsonify, request, send_file

app = Flask(__name__)
BASE = os.path.dirname(os.path.abspath(__file__))
ROOMS = {}
LOCK = threading.RLock()
MAX_PLAYERS = 4

# 104 fichas numeradas (2 copias x 4 colores x 13) + 2 comodines (n=0)
TILES, _i = {}, 0
for _ in range(2):
    for c in range(4):
        for n in range(1, 14):
            TILES[_i] = {"id": _i, "n": n, "c": c}
            _i += 1
for _ in range(2):
    TILES[_i] = {"id": _i, "n": 0, "c": -1}
    _i += 1


def locked(f):
    @wraps(f)
    def w(*a, **k):
        with LOCK:
            return f(*a, **k)
    return w


def tl(ids):
    return [TILES[int(i)] for i in ids]


def err(msg, code=400, **extra):
    return jsonify(error=msg, **extra), code


# ---------- reglas ----------
def group_value(g):
    """Valor de la combinación (escalera o grupo) o None si no es válida."""
    if len(g) < 3:
        return None
    reg = [t for t in g if t["n"]]
    if not reg:
        return None
    nums = [t["n"] for t in reg]
    cols = {t["c"] for t in reg}
    if len(set(nums)) == 1:  # grupo: mismo número, colores distintos
        if len(g) > 4 or len(cols) != len(reg):
            return None
        return nums[0] * len(g)
    if len(cols) != 1 or len(set(nums)) != len(nums) or len(g) > 13:
        return None  # escalera: mismo color, consecutivos
    if max(nums) - min(nums) + 1 > len(g):
        return None
    start = min(min(nums), 14 - len(g))
    return sum(range(start, start + len(g)))


def candidates(ts):
    out, reg = [], [t for t in ts if t["n"]]
    for c in range(4):
        by = {}
        for t in reg:
            if t["c"] == c:
                by.setdefault(t["n"], t)
        n = 1
        while n <= 13:
            if n in by:
                run = []
                while n <= 13 and n in by:
                    run.append(by[n])
                    n += 1
                if len(run) >= 3:
                    out.append(run)
            else:
                n += 1
    for n in range(1, 14):
        by = {}
        for t in reg:
            if t["n"] == n:
                by.setdefault(t["c"], t)
        if len(by) >= 3:
            out.append(list(by.values()))
    return out


def points(ids):
    return sum(TILES[i]["n"] or 30 for i in ids)


def draw(R, p):
    if R["pool"]:
        p["rack"].append(R["pool"].pop())
        return True
    return False


def check_stuck(R):
    """Mazo vacío y todos pasan: gana quien tenga menos puntos."""
    if not R["winner"] and not R["pool"] and R["passes"] >= len(R["players"]):
        best = sorted(R["players"], key=lambda q: points(q["rack"]))
        tie = len(best) > 1 and points(best[0]["rack"]) == points(best[1]["rack"])
        R["winner"] = "draw" if tie else best[0]["id"]


def bot_turn(R, p):
    rem, groups = tl(p["rack"]), []
    while True:
        cands = candidates(rem)
        if not cands:
            break
        best = max(cands, key=group_value)
        groups.append(best)
        used = {t["id"] for t in best}
        rem = [t for t in rem if t["id"] not in used]
    if not p["melded"]:
        if sum(group_value(g) for g in groups) < 30:
            groups, rem = [], tl(p["rack"])
        elif groups:
            p["melded"] = True
    table = [tl(g) for g in R["table"]]
    played = sum(len(g) for g in groups)
    if p["melded"]:  # añade fichas sueltas a combinaciones existentes
        for t in rem[:]:
            for g in table + groups:
                if group_value(g + [t]):
                    g.append(t)
                    rem.remove(t)
                    played += 1
                    break
    if played:
        R["table"] = [[t["id"] for t in sorted(g, key=lambda t: t["n"] or 99)]
                      for g in table + groups]
        p["rack"] = [t["id"] for t in rem]
        R["passes"] = 0
        R["log"].append(f"{p['name']} jugó {played} ficha(s).")
        if not rem:
            R["winner"] = p["id"]
            R["log"].append(f"¡{p['name']} ganó!")
    elif draw(R, p):
        R["passes"] = 0
        R["log"].append(f"{p['name']} robó una ficha.")
    else:
        R["passes"] += 1
        R["log"].append(f"{p['name']} pasó.")
    check_stuck(R)


def end_turn(R):
    """Pasa el turno; los bots juegan al instante."""
    check_stuck(R)
    while not R["winner"]:
        R["turn"] = (R["turn"] + 1) % len(R["players"])
        p = R["players"][R["turn"]]
        if not p["bot"]:
            break
        bot_turn(R, p)
    R["v"] += 1


# ---------- salas ----------
def clean(name, default="Jugador"):
    return (str(name or "").strip()[:20]) or default


def new_player(name, bot=False):
    return dict(id=uuid.uuid4().hex[:12], name=name, rack=[], melded=False, bot=bot)


def ctx():
    d = request.get_json(silent=True) or request.args
    R = ROOMS.get(str(d.get("code", "")).upper())
    p = next((q for q in R["players"] if q["id"] == d.get("pid")), None) if R else None
    return d, R, p


def view(R, p):
    w = R["winner"]
    cur = R["players"][R["turn"]] if R["started"] else None
    return {
        "v": R["v"], "code": R["code"], "started": R["started"],
        "host": p["id"] == R["host"],
        "players": [{"name": q["name"], "n": len(q["rack"]), "bot": q["bot"],
                     "you": q is p} for q in R["players"]],
        "turn": R["turn"], "myTurn": bool(cur is p and not w),
        "rack": [TILES[i] for i in p["rack"]],
        "table": [[TILES[i] for i in g] for g in R["table"]],
        "pool": len(R["pool"]), "melded": p["melded"],
        "winner": None if not w else "draw" if w == "draw" else
        {"name": next(q["name"] for q in R["players"] if q["id"] == w), "you": w == p["id"]},
        "log": R["log"][-5:],
    }


@app.get("/")
@app.get("/r/<code>")
def index(code=None):
    return send_file(os.path.join(BASE, "index.html"))


@app.post("/api/create")
@locked
def create():
    if len(ROOMS) > 100:
        ROOMS.pop(next(iter(ROOMS)))
    code = "".join(random.choices("ABCDEFGHJKLMNPQRSTUVWXYZ23456789", k=5))
    p = new_player(clean((request.get_json(silent=True) or {}).get("name")))
    ROOMS[code] = dict(code=code, players=[p], host=p["id"], started=False, winner=None,
                       pool=[], table=[], turn=0, passes=0, v=0, log=[])
    return jsonify(code=code, pid=p["id"])


@app.post("/api/join")
@locked
def join():
    d = request.get_json(silent=True) or {}
    R = ROOMS.get(str(d.get("code", "")).upper())
    if not R:
        return err("Esa sala no existe.", 404)
    if R["started"]:
        return err("La partida ya empezó.")
    if len(R["players"]) >= MAX_PLAYERS:
        return err("La sala está llena (máximo 4).")
    p = new_player(clean(d.get("name")))
    R["players"].append(p)
    R["v"] += 1
    return jsonify(pid=p["id"])


@app.post("/api/addbot")
@locked
def addbot():
    d, R, p = ctx()
    if not p or p["id"] != R["host"] or R["started"] or len(R["players"]) >= MAX_PLAYERS:
        return err("No se puede agregar un bot ahora.")
    n = sum(q["bot"] for q in R["players"]) + 1
    R["players"].append(new_player(f"Bot {n}", bot=True))
    R["v"] += 1
    return jsonify(ok=True)


@app.post("/api/start")
@locked
def start():
    d, R, p = ctx()
    if not p or p["id"] != R["host"] or R["started"]:
        return err("Solo el anfitrión puede empezar.")
    if len(R["players"]) < 2:
        return err("Necesitas al menos 2 jugadores (puedes agregar un bot).")
    ids = list(TILES)
    random.shuffle(ids)
    for q in R["players"]:
        q["rack"], ids = ids[:14], ids[14:]
    R.update(pool=ids, started=True, turn=0, log=["¡Empieza la partida!"])
    if R["players"][0]["bot"]:  # por si algún día el primero fuera bot
        R["turn"] = -1
        end_turn(R)
    R["v"] += 1
    return jsonify(view(R, p))


@app.get("/api/state")
@locked
def state():
    d, R, p = ctx()
    if not R:
        return err("Esa sala no existe.", 404, gone=True)
    if not p:
        return err("No formas parte de esta sala.", 404, nojoin=True)
    return jsonify(view(R, p))


@app.post("/api/check")
def check():
    return jsonify([group_value(tl(g)) for g in request.json["table"]])


@app.post("/api/play")
@locked
def play():
    d, R, p = ctx()
    if not p or not R["started"] or R["winner"]:
        return err("La partida no está disponible.")
    if R["players"][R["turn"]] is not p:
        return err("No es tu turno.")
    new = [[int(i) for i in g] for g in d["table"] if g]
    old = R["table"]
    new_ids = [i for g in new for i in g]
    old_ids = [i for g in old for i in g]
    if len(set(new_ids)) != len(new_ids):
        return err("Hay fichas repetidas.")
    if not set(old_ids) <= set(new_ids):
        return err("No puedes quitar fichas de la mesa.")
    added = set(new_ids) - set(old_ids)
    if not added or not added <= set(p["rack"]):
        return err("Debes jugar al menos una ficha de tu atril.")
    vals = [group_value(tl(g)) for g in new]
    if None in vals:
        return err("Hay combinaciones inválidas (marcadas en rojo).")
    if not p["melded"]:
        olds = {frozenset(g) for g in old}
        news = [frozenset(g) for g in new]
        if not olds <= set(news):
            return err("En tu primera jugada no puedes mover fichas de la mesa.")
        pts = sum(v for g, v in zip(news, vals) if g not in olds)
        if pts < 30:
            return err(f"La primera jugada debe sumar 30 o más (llevas {pts}).")
        p["melded"] = True
    R["table"] = new
    p["rack"] = [i for i in p["rack"] if i not in added]
    R["passes"] = 0
    R["log"].append(f"{p['name']} jugó {len(added)} ficha(s).")
    if not p["rack"]:
        R["winner"] = p["id"]
        R["log"].append(f"¡{p['name']} ganó!")
    end_turn(R)
    return jsonify(view(R, p))


@app.post("/api/draw")
@locked
def draw_tile():
    d, R, p = ctx()
    if not p or not R["started"] or R["winner"]:
        return err("La partida no está disponible.")
    if R["players"][R["turn"]] is not p:
        return err("No es tu turno.")
    if draw(R, p):
        R["passes"] = 0
        R["log"].append(f"{p['name']} robó una ficha.")
    else:
        R["passes"] += 1
        R["log"].append(f"{p['name']} pasó (mazo vacío).")
    end_turn(R)
    return jsonify(view(R, p))


@app.errorhandler(KeyError)
@app.errorhandler(TypeError)
@app.errorhandler(ValueError)
def bad_request(_):
    return err("Solicitud inválida.")


if __name__ == "__main__":
    app.run(debug=True)
