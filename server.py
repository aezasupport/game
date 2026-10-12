"""Attribute Arena Online — сервер (FastAPI + WebSocket + SQLite).
Запуск: uvicorn server:app --host 0.0.0.0 --port 8000
Переменные окружения: BOT_TOKEN (обязательно), DB_PATH (необязательно), DEV=1 (тест без Telegram)."""
import asyncio
import hashlib
import hmac
import json
import os
import random
import sqlite3
import time
import urllib.request
from pathlib import Path
from urllib.parse import parse_qsl, unquote

from fastapi import FastAPI, Header, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse
from pydantic import BaseModel

from game import Game, Player, ai_turn

TOKEN = os.getenv("BOT_TOKEN", "")
DEV = os.getenv("DEV") == "1"
BASE = Path(__file__).parent
HEROES = ("str", "agi", "int")
BOT_USER = ""

app = FastAPI()
db = sqlite3.connect(os.getenv("DB_PATH", str(BASE / "arena.db")), check_same_thread=False)
db.row_factory = sqlite3.Row
db.executescript("""
CREATE TABLE IF NOT EXISTS users(id INTEGER PRIMARY KEY, name TEXT, username TEXT,
  wins INTEGER DEFAULT 0, losses INTEGER DEFAULT 0, rating INTEGER DEFAULT 1000);
CREATE TABLE IF NOT EXISTS friends(a INTEGER, b INTEGER, PRIMARY KEY(a, b));
""")

conns: dict = {}    # uid -> WebSocket
queue: list = []    # [(uid, attr)]
invites: dict = {}  # кому -> (от кого, герой)
ugame: dict = {}    # uid -> Game


# ---------- авторизация через Telegram initData ----------
def auth(init: str):
    if DEV and init.startswith("dev:"):
        _, uid, name = init.split(":", 2)
        return int(uid), unquote(name), ""
    d = dict(parse_qsl(init, keep_blank_values=True))
    h = d.pop("hash", "")
    check = "\n".join(f"{k}={v}" for k, v in sorted(d.items()))
    secret = hmac.new(b"WebAppData", TOKEN.encode(), hashlib.sha256).digest()
    ok = hmac.compare_digest(hmac.new(secret, check.encode(), hashlib.sha256).hexdigest(), h)
    if not ok or time.time() - int(d.get("auth_date", 0)) > 86400:
        raise ValueError("bad auth")
    u = json.loads(d["user"])
    return u["id"], u.get("first_name", "Игрок"), u.get("username", "")


def touch(uid, name, uname):
    db.execute("INSERT INTO users(id,name,username) VALUES(?,?,?) "
               "ON CONFLICT(id) DO UPDATE SET name=excluded.name, username=excluded.username",
               (uid, name, uname))
    db.commit()


def who(init: str):
    try:
        uid, name, un = auth(init)
    except Exception:
        raise HTTPException(401, "auth")
    touch(uid, name, un)
    return uid, name


def uname(uid):
    r = db.execute("SELECT name FROM users WHERE id=?", (uid,)).fetchone()
    return r["name"] if r else "Игрок"


# ---------- REST ----------
@app.on_event("startup")
def startup():
    global BOT_USER
    try:
        url = f"https://api.telegram.org/bot{TOKEN}/getMe"
        BOT_USER = json.load(urllib.request.urlopen(url, timeout=8))["result"]["username"]
    except Exception:
        pass


@app.get("/")
def index():
    return FileResponse(BASE / "static" / "index.html")


@app.get("/api/me")
def api_me(x_init_data: str = Header("")):
    uid, _ = who(x_init_data)
    me = dict(db.execute("SELECT * FROM users WHERE id=?", (uid,)).fetchone())
    fr = db.execute("SELECT u.id,u.name,u.wins,u.losses,u.rating FROM friends f "
                    "JOIN users u ON u.id=f.b WHERE f.a=? ORDER BY u.rating DESC", (uid,)).fetchall()
    return {"me": me, "bot": BOT_USER,
            "friends": [dict(r) | {"online": r["id"] in conns} for r in fr]}


class FriendIn(BaseModel):
    id: int


@app.post("/api/friend")
def api_friend(body: FriendIn, x_init_data: str = Header("")):
    uid, _ = who(x_init_data)
    other = db.execute("SELECT name FROM users WHERE id=?", (body.id,)).fetchone()
    if not other or body.id == uid:
        raise HTTPException(404, "not found")
    db.executemany("INSERT OR IGNORE INTO friends(a,b) VALUES(?,?)", [(uid, body.id), (body.id, uid)])
    db.commit()
    return {"ok": True, "name": other["name"]}


@app.get("/api/top")
def api_top(x_init_data: str = Header("")):
    who(x_init_data)
    rows = db.execute("SELECT id,name,wins,losses,rating FROM users "
                      "ORDER BY rating DESC, wins DESC LIMIT 10").fetchall()
    return [dict(r) for r in rows]


# ---------- представление партии для клиента ----------
def view(g: Game, pi: int):
    me, op = g.players[pi], g.players[1 - pi]

    def mn(m, mine):
        return {"n": m.card.name, "at": m.atk, "h": m.hp, "t": m.taunt,
                "hs": "haste" in m.card.kw, "r": bool(m.can_attack) if mine else False}

    ready = g.turn == pi and g.winner is None
    return {
        "turn": ready, "no": g.turn_no,
        "win": None if g.winner is None else (2 if g.winner == 2 else int(g.winner == pi)),
        "me": {"n": me.name, "a": me.attr, "hp": me.hp, "max": me.max_hp, "mana": me.mana,
               "mm": me.max_mana, "ch": me.charge, "deck": len(me.deck)},
        "op": {"n": op.name, "a": op.attr, "hp": op.hp, "max": op.max_hp,
               "hand": len(op.hand), "deck": len(op.deck)},
        "hand": [{"n": c.name, "c": c.cost, "k": c.kind, "at": c.atk, "h": c.hp,
                  "tx": c.text, "ic": c.attr} for c in me.hand],
        "mb": [mn(m, True) for m in me.board], "ob": [mn(m, False) for m in op.board],
        "tg": {str(i): g.legal_targets(pi, i) for i, m in enumerate(me.board) if m.can_attack} if ready else {},
        "log": g.log[-6:],
    }


async def send(uid, obj):
    ws = conns.get(uid)
    if ws:
        try:
            await ws.send_text(json.dumps(obj))
        except Exception:
            pass


async def push(g: Game):
    for pi, uid in enumerate(g.uids):
        if uid:
            await send(uid, {"t": "state", "s": view(g, pi)})


async def finish(g: Game):
    if g.done:
        return
    g.done = True
    humans = [u for u in g.uids if u]
    if g.winner in (0, 1):
        w, l = g.uids[g.winner], g.uids[1 - g.winner]
        if w:
            db.execute("UPDATE users SET wins=wins+1, rating=rating+? WHERE id=?",
                       (25 if len(humans) == 2 else 0, w))
        if l:
            db.execute("UPDATE users SET losses=losses+1, rating=MAX(0,rating-?) WHERE id=?",
                       (20 if len(humans) == 2 else 0, l))
        db.commit()
    for u in humans:
        if ugame.get(u) is g:
            del ugame[u]


async def leave(uid):
    g = ugame.get(uid)
    if g and not g.done:
        g.surrender(g.uids.index(uid))
        await push(g)
        await finish(g)


async def bot_turns(g: Game):
    while g.winner is None and g.players[g.turn].is_bot and not g.done:
        await asyncio.sleep(1.2)
        if g.done:
            return
        ai_turn(g, g.turn)
        await push(g)
    if g.winner is not None:
        await finish(g)


async def begin(a, attr_a, b, attr_b):
    for u in (a, b):
        if u:
            await leave(u)
    pa = Player(a, uname(a), attr_a)
    pb = Player(b, uname(b) if b else "Ворон-бот", attr_b, is_bot=not b)
    g = Game(pa, pb)
    g.uids, g.done = [a, b], False
    for u in (a, b):
        if u:
            ugame[u] = g
    await push(g)
    await bot_turns(g)


async def act(uid, m):
    g = ugame.get(uid)
    if not g or g.done:
        return
    pi, t, err = g.uids.index(uid), m.get("t"), None
    if t == "surr":
        g.surrender(pi)
    elif t == "play":
        err = g.play_card(pi, int(m["i"]))
    elif t == "atk":
        err = g.attack(pi, int(m["m"]), int(m["to"]))
    elif t == "ult":
        err = g.ultimate(pi)
    elif t == "end":
        err = g.end_turn(pi)
    else:
        return
    if err:
        await send(uid, {"t": "err", "m": err})
        return
    await push(g)
    if g.winner is not None:
        await finish(g)
    else:
        await bot_turns(g)


async def grace(uid, g):
    await asyncio.sleep(45)  # время на переподключение
    if uid in conns or g.done or ugame.get(uid) is not g:
        return
    if all(g.uids):
        await leave(uid)
    else:
        g.done = True
        ugame.pop(uid, None)


# ---------- WebSocket ----------
@app.websocket("/ws")
async def ws_endpoint(ws: WebSocket):
    await ws.accept()
    try:
        uid, name, un = auth(ws.query_params.get("auth", ""))
        touch(uid, name, un)
    except Exception:
        await ws.close(code=4401)
        return
    conns[uid] = ws
    if uid in ugame:
        await push(ugame[uid])  # возвращаемся в идущую партию
    try:
        while True:
            m = json.loads(await ws.receive_text())
            t, a = m.get("t"), m.get("a")
            if t == "queue" and a in HEROES:
                queue[:] = [q for q in queue if q[0] != uid]
                if queue:
                    o = queue.pop(0)
                    await begin(o[0], o[1], uid, a)
                else:
                    queue.append((uid, a))
                    await send(uid, {"t": "queued"})
            elif t == "cancel":
                queue[:] = [q for q in queue if q[0] != uid]
            elif t == "bot" and a in HEROES:
                queue[:] = [q for q in queue if q[0] != uid]
                await begin(uid, a, 0, random.choice(HEROES))
            elif t == "chal" and a in HEROES:
                f = int(m.get("id", 0))
                ok = db.execute("SELECT 1 FROM friends WHERE a=? AND b=?", (uid, f)).fetchone()
                if ok and f in conns and f != uid:
                    invites[f] = (uid, a)
                    await send(f, {"t": "invite", "from": uid, "name": name})
                    await send(uid, {"t": "info", "m": "Приглашение отправлено"})
                else:
                    await send(uid, {"t": "err", "m": "Друг сейчас не в сети"})
            elif t == "accept" and a in HEROES:
                inv = invites.pop(uid, None)
                if inv and inv[0] in conns:
                    await begin(inv[0], inv[1], uid, a)
                else:
                    await send(uid, {"t": "err", "m": "Приглашение устарело"})
            elif t == "decline":
                inv = invites.pop(uid, None)
                if inv:
                    await send(inv[0], {"t": "info", "m": f"{name} отклонил(а) вызов"})
            else:
                await act(uid, m)
    except WebSocketDisconnect:
        pass
    except Exception as ex:  # не роняем сервер из-за одного клиента
        print("ws error:", ex)
    finally:
        if conns.get(uid) is ws:
            conns.pop(uid, None)
            queue[:] = [q for q in queue if q[0] != uid]
            invites.pop(uid, None)
            if uid in ugame:
                asyncio.create_task(grace(uid, ugame[uid]))
