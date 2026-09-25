import json
import os
from datetime import datetime, timedelta, timezone

import psycopg
from fastapi import Depends, FastAPI, HTTPException
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from jose import JWTError, jwt
from passlib.context import CryptContext
from pydantic import BaseModel, Field
from psycopg.rows import dict_row

from rules import judge

SECRET = os.environ.get("JWT_SECRET", "herb-process-dev-secret")
DSN = os.environ.get("DATABASE_URL", "postgresql://app:app@localhost:54393/herb")
pwd = CryptContext(schemes=["bcrypt"], deprecated="auto")
security = HTTPBearer(auto_error=False)
USERS = {
    "processor": {"role": "writer", "password_hash": pwd.hash("herb123456")},
    "processor2": {"role": "writer", "password_hash": pwd.hash("herb123456")},
    "checker": {"role": "reader", "password_hash": pwd.hash("check123456")},
}


def connect():
    return psycopg.connect(DSN, row_factory=dict_row)


class LoginIn(BaseModel):
    username: str
    password: str


class StepIn(BaseModel):
    name: str
    temp_c: float
    minutes: float


class BatchIn(BaseModel):
    herb: str = Field(min_length=1, max_length=80)
    plant: str = Field(min_length=1, max_length=80, default="甲厂")


class TransferIn(BaseModel):
    source_plant: str = Field(min_length=1, max_length=80)
    target_plant: str = Field(min_length=1, max_length=80)
    herb: str = Field(min_length=1, max_length=80)


def current_user(credentials: HTTPAuthorizationCredentials | None = Depends(security)) -> dict:
    if credentials is None:
        raise HTTPException(status_code=401, detail="未登录")
    try:
        payload = jwt.decode(credentials.credentials, SECRET, algorithms=["HS256"])
    except JWTError as exc:
        raise HTTPException(status_code=401, detail="无效令牌") from exc
    if payload.get("sub") not in USERS:
        raise HTTPException(status_code=401, detail="无效令牌")
    return {"username": payload["sub"], "role": payload.get("role")}


def require_writer(user: dict = Depends(current_user)) -> dict:
    if user["role"] != "writer":
        raise HTTPException(status_code=403, detail="仅炮制员可操作")
    return user


app = FastAPI(title="饮片炮制记录台")


@app.on_event("startup")
def startup():
    with connect() as conn:
        conn.execute(
            """CREATE TABLE IF NOT EXISTS batches (
                id serial PRIMARY KEY,
                herb text NOT NULL,
                doc jsonb NOT NULL,
                verdict text NOT NULL,
                reason text NOT NULL,
                created_by text NOT NULL,
                created_at timestamptz NOT NULL
            )"""
        )
        conn.execute("ALTER TABLE batches ADD COLUMN IF NOT EXISTS plant text NOT NULL DEFAULT '甲厂'")
        conn.execute(
            """CREATE TABLE IF NOT EXISTS transfers (
                id serial PRIMARY KEY,
                source_plant text NOT NULL,
                target_plant text NOT NULL,
                herb text NOT NULL,
                batch_id integer,
                status text NOT NULL DEFAULT 'pending',
                created_by text NOT NULL,
                confirmed_by text,
                created_at timestamptz NOT NULL,
                confirmed_at timestamptz
            )"""
        )
        conn.execute(
            """CREATE TABLE IF NOT EXISTS transfer_logs (
                id serial PRIMARY KEY,
                transfer_id integer NOT NULL,
                action text NOT NULL,
                actor text NOT NULL,
                detail text NOT NULL,
                created_at timestamptz NOT NULL
            )"""
        )
        count = conn.execute("SELECT COUNT(*) AS n FROM batches").fetchone()["n"]
        if count == 0:
            now = datetime.now(timezone.utc)
            samples = [
                ("甘草", "甲厂", {"steps": [{"name": "清炒", "temp_c": 120, "minutes": 12}]}),
                ("黄芩", "甲厂", {"steps": [{"name": "清炒", "temp_c": 40, "minutes": 12}]}),
            ]
            for herb, plant, doc in samples:
                verdict, reason = judge(doc)
                conn.execute(
                    """INSERT INTO batches (herb, plant, doc, verdict, reason, created_by, created_at)
                       VALUES (%s, %s, %s::jsonb, %s, %s, %s, %s)""",
                    (herb, plant, json.dumps(doc, ensure_ascii=False), verdict, reason, "processor", now),
                )
        conn.commit()


def current_locations(conn) -> dict[int, str]:
    """按已生效调拨推算每条批次当前所在厂。未确认调拨不参与，结果两边不变。"""
    locations = {
        r["id"]: r["plant"]
        for r in conn.execute("SELECT id, plant FROM batches").fetchall()
    }
    moves = conn.execute(
        """SELECT batch_id, target_plant FROM transfers
           WHERE status = 'effective' AND batch_id IS NOT NULL
           ORDER BY confirmed_at, id"""
    ).fetchall()
    for move in moves:
        if move["batch_id"] in locations:
            locations[move["batch_id"]] = move["target_plant"]
    return locations


@app.get("/api/health")
def health():
    return {"status": "ok", "service": "herb-process-record"}


@app.post("/api/auth/login")
def login(body: LoginIn):
    user = USERS.get(body.username.strip())
    if not user or not pwd.verify(body.password, user["password_hash"]):
        raise HTTPException(status_code=401, detail="用户名或密码错误")
    exp = datetime.now(timezone.utc) + timedelta(hours=8)
    token = jwt.encode({"sub": body.username.strip(), "role": user["role"], "exp": exp}, SECRET, algorithm="HS256")
    return {"access_token": token, "username": body.username.strip(), "role": user["role"]}


@app.get("/api/batches")
def list_batches(plant: str | None = None, _user: dict = Depends(current_user)):
    # 厂名筛选在服务端完成：按已生效调拨算当前所在厂，源厂藏行、目标厂可见。
    with connect() as conn:
        rows = conn.execute(
            "SELECT id, herb, plant, doc, verdict, reason, created_by FROM batches ORDER BY id DESC"
        ).fetchall()
        locations = current_locations(conn)
    result = []
    for row in rows:
        current_plant = locations.get(row["id"], row["plant"])
        if plant and current_plant != plant.strip():
            continue
        row = dict(row)
        row["current_plant"] = current_plant
        result.append(row)
    return result


@app.post("/api/batches", status_code=201)
def create_batch(body: BatchIn, user: dict = Depends(require_writer)):
    doc = {"steps": [s.model_dump() for s in body.steps]}
    verdict, reason = judge(doc)
    with connect() as conn:
        row = conn.execute(
            """INSERT INTO batches (herb, plant, doc, verdict, reason, created_by, created_at)
               VALUES (%s, %s, %s::jsonb, %s, %s, %s, %s)
               RETURNING id, herb, plant, doc, verdict, reason, created_by""",
            (body.herb.strip(), body.plant.strip(), json.dumps(doc, ensure_ascii=False),
             verdict, reason, user["username"], datetime.now(timezone.utc)),
        ).fetchone()
        conn.commit()
    return row


@app.get("/api/transfers")
def list_transfers(_user: dict = Depends(current_user)):
    with connect() as conn:
        transfers = conn.execute(
            "SELECT * FROM transfers ORDER BY id DESC"
        ).fetchall()
        logs = conn.execute(
            "SELECT * FROM transfer_logs ORDER BY id DESC"
        ).fetchall()
    return {"transfers": transfers, "logs": logs}


@app.post("/api/transfers", status_code=201)
def create_transfer(body: TransferIn, user: dict = Depends(require_writer)):
    source = body.source_plant.strip()
    target = body.target_plant.strip()
    herb = body.herb.strip()
    if source == target:
        raise HTTPException(status_code=400, detail="源厂与目标厂不能相同")
    now = datetime.now(timezone.utc)
    with connect() as conn:
        row = conn.execute(
            """INSERT INTO transfers (source_plant, target_plant, herb, status, created_by, created_at)
               VALUES (%s, %s, %s, 'pending', %s, %s)
               RETURNING *""",
            (source, target, herb, user["username"], now),
        ).fetchone()
        conn.execute(
            """INSERT INTO transfer_logs (transfer_id, action, actor, detail, created_at)
               VALUES (%s, 'created', %s, %s, %s)""",
            (row["id"], user["username"], f"建单：{source} → {target} · {herb}", now),
        )
        conn.commit()
    return row


@app.post("/api/transfers/{transfer_id}/confirm")
def confirm_transfer(transfer_id: int, user: dict = Depends(require_writer)):
    now = datetime.now(timezone.utc)
    with connect() as conn:
        transfer = conn.execute(
            "SELECT * FROM transfers WHERE id = %s FOR UPDATE", (transfer_id,)
        ).fetchone()
        if transfer is None:
            raise HTTPException(status_code=404, detail="调拨单不存在")
        if transfer["status"] != "pending":
            raise HTTPException(status_code=400, detail="该调拨单已生效")
        # 双岗确认：建单人不能自己确认，必须另一名炮制员账号。
        if transfer["created_by"] == user["username"]:
            raise HTTPException(status_code=403, detail="建单人不能自行确认，请换另一名炮制员账号")
        locations = current_locations(conn)
        candidates = conn.execute(
            "SELECT id FROM batches WHERE herb = %s ORDER BY id DESC", (transfer["herb"],)
        ).fetchall()
        target_batch = next(
            (r["id"] for r in candidates if locations.get(r["id"]) == transfer["source_plant"]),
            None,
        )
        if target_batch is None:
            raise HTTPException(status_code=400, detail="源厂当前没有该饮片的记录行")
        conn.execute(
            """UPDATE transfers
               SET status = 'effective', confirmed_by = %s, confirmed_at = %s, batch_id = %s
               WHERE id = %s""",
            (user["username"], now, target_batch, transfer_id),
        )
        conn.execute(
            """INSERT INTO transfer_logs (transfer_id, action, actor, detail, created_at)
               VALUES (%s, 'confirmed', %s, %s, %s)""",
            (transfer_id, user["username"],
             f"确认生效：{transfer['source_plant']} → {transfer['target_plant']} · {transfer['herb']}", now),
        )
        conn.commit()
        row = conn.execute("SELECT * FROM transfers WHERE id = %s", (transfer_id,)).fetchone()
    return row
