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
ROLES = ("writer", "reader")  # writer=炮制员 reader=质检员
SEED_USERS = [
    ("processor", "herb123456", "writer"),
    ("checker", "check123456", "reader"),
]


def connect():
    return psycopg.connect(DSN, row_factory=dict_row)


class LoginIn(BaseModel):
    username: str
    password: str


class UserIn(BaseModel):
    username: str = Field(min_length=2, max_length=40)
    password: str = Field(min_length=6, max_length=80)
    role: str = "writer"


class StepIn(BaseModel):
    name: str
    temp_c: float
    minutes: float


class BatchIn(BaseModel):
    herb: str = Field(min_length=1, max_length=80)
    factory: str = Field(min_length=1, max_length=80)
    steps: list[StepIn]


class TransferIn(BaseModel):
    herb: str = Field(min_length=1, max_length=80)
    source_factory: str = Field(min_length=1, max_length=80)
    target_factory: str = Field(min_length=1, max_length=80)


def current_user(credentials: HTTPAuthorizationCredentials | None = Depends(security)) -> dict:
    if credentials is None:
        raise HTTPException(status_code=401, detail="未登录")
    try:
        payload = jwt.decode(credentials.credentials, SECRET, algorithms=["HS256"])
    except JWTError as exc:
        raise HTTPException(status_code=401, detail="无效令牌") from exc
    with connect() as conn:
        row = conn.execute(
            "SELECT username, role FROM users WHERE username = %s", (payload.get("sub"),)
        ).fetchone()
    if row is None:
        raise HTTPException(status_code=401, detail="无效令牌")
    return {"username": row["username"], "role": row["role"]}


def require_writer(user: dict = Depends(current_user)) -> dict:
    if user["role"] != "writer":
        raise HTTPException(status_code=403, detail="仅炮制员可执行此操作")
    return user


app = FastAPI(title="饮片炮制记录台")


@app.on_event("startup")
def startup():
    with connect() as conn:
        conn.execute(
            """CREATE TABLE IF NOT EXISTS users (
                username text PRIMARY KEY,
                password_hash text NOT NULL,
                role text NOT NULL
            )"""
        )
        for username, password, role in SEED_USERS:
            conn.execute(
                """INSERT INTO users (username, password_hash, role)
                   VALUES (%s, %s, %s)
                   ON CONFLICT (username) DO NOTHING""",
                (username, pwd.hash(password), role),
            )
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
        conn.execute(
            "ALTER TABLE batches ADD COLUMN IF NOT EXISTS factory text NOT NULL DEFAULT '甲厂'"
        )
        conn.execute(
            """CREATE TABLE IF NOT EXISTS transfers (
                id serial PRIMARY KEY,
                herb text NOT NULL,
                source_factory text NOT NULL,
                target_factory text NOT NULL,
                status text NOT NULL DEFAULT 'pending',
                created_by text NOT NULL,
                created_at timestamptz NOT NULL,
                confirmed_by text,
                confirmed_at timestamptz
            )"""
        )
        conn.execute(
            """CREATE TABLE IF NOT EXISTS transfer_events (
                id serial PRIMARY KEY,
                transfer_id integer NOT NULL REFERENCES transfers (id),
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
                ("黄芩", "乙厂", {"steps": [{"name": "清炒", "temp_c": 40, "minutes": 12}]}),
            ]
            for herb, factory, doc in samples:
                verdict, reason = judge(doc)
                conn.execute(
                    """INSERT INTO batches (herb, factory, doc, verdict, reason, created_by, created_at)
                       VALUES (%s, %s, %s::jsonb, %s, %s, %s, %s)""",
                    (herb, factory, json.dumps(doc, ensure_ascii=False), verdict, reason, "processor", now),
                )
        conn.commit()


@app.get("/api/health")
def health():
    return {"status": "ok", "service": "herb-process-record"}


@app.post("/api/auth/login")
def login(body: LoginIn):
    username = body.username.strip()
    with connect() as conn:
        row = conn.execute(
            "SELECT username, password_hash, role FROM users WHERE username = %s", (username,)
        ).fetchone()
    if not row or not pwd.verify(body.password, row["password_hash"]):
        raise HTTPException(status_code=401, detail="用户名或密码错误")
    exp = datetime.now(timezone.utc) + timedelta(hours=8)
    token = jwt.encode({"sub": row["username"], "role": row["role"], "exp": exp}, SECRET, algorithm="HS256")
    return {"access_token": token, "username": row["username"], "role": row["role"]}


@app.post("/api/users", status_code=201)
def create_user(body: UserIn, _user: dict = Depends(require_writer)):
    if body.role not in ROLES:
        raise HTTPException(status_code=400, detail="角色只能是 writer（炮制员）或 reader（质检员）")
    username = body.username.strip()
    with connect() as conn:
        exists = conn.execute("SELECT 1 FROM users WHERE username = %s", (username,)).fetchone()
        if exists:
            raise HTTPException(status_code=409, detail="用户名已存在")
        conn.execute(
            "INSERT INTO users (username, password_hash, role) VALUES (%s, %s, %s)",
            (username, pwd.hash(body.password), body.role),
        )
        conn.commit()
    return {"username": username, "role": body.role}


@app.get("/api/batches")
def list_batches(factory: str | None = None, _user: dict = Depends(current_user)):
    sql = "SELECT id, herb, factory, doc, verdict, reason, created_by FROM batches"
    params = ()
    if factory and factory.strip():
        sql += " WHERE factory = %s"
        params = (factory.strip(),)
    sql += " ORDER BY id DESC"
    with connect() as conn:
        rows = conn.execute(sql, params).fetchall()
    return rows


@app.post("/api/batches", status_code=201)
def create_batch(body: BatchIn, user: dict = Depends(require_writer)):
    doc = {"steps": [s.model_dump() for s in body.steps]}
    verdict, reason = judge(doc)
    with connect() as conn:
        row = conn.execute(
            """INSERT INTO batches (herb, factory, doc, verdict, reason, created_by, created_at)
               VALUES (%s, %s, %s::jsonb, %s, %s, %s, %s)
               RETURNING id, herb, factory, doc, verdict, reason, created_by""",
            (
                body.herb.strip(),
                body.factory.strip(),
                json.dumps(doc, ensure_ascii=False),
                verdict,
                reason,
                user["username"],
                datetime.now(timezone.utc),
            ),
        ).fetchone()
        conn.commit()
    return row


def transfer_row(transfer_id: int) -> dict | None:
    with connect() as conn:
        return conn.execute(
            """SELECT id, herb, source_factory, target_factory, status,
                      created_by, created_at, confirmed_by, confirmed_at
               FROM transfers WHERE id = %s""",
            (transfer_id,),
        ).fetchone()


def log_event(conn, transfer_id: int, action: str, actor: str, herb: str, source: str, target: str):
    conn.execute(
        """INSERT INTO transfer_events (transfer_id, action, actor, detail, created_at)
           VALUES (%s, %s, %s, %s, %s)""",
        (
            transfer_id,
            action,
            actor,
            f"{herb}：{source} → {target}",
            datetime.now(timezone.utc),
        ),
    )


@app.post("/api/transfers", status_code=201)
def create_transfer(body: TransferIn, user: dict = Depends(require_writer)):
    herb = body.herb.strip()
    source = body.source_factory.strip()
    target = body.target_factory.strip()
    if source == target:
        raise HTTPException(status_code=400, detail="源厂与目标厂不能相同")
    with connect() as conn:
        row = conn.execute(
            """INSERT INTO transfers (herb, source_factory, target_factory, status, created_by, created_at)
               VALUES (%s, %s, %s, 'pending', %s, %s)
               RETURNING id, herb, source_factory, target_factory, status, created_by, created_at""",
            (herb, source, target, user["username"], datetime.now(timezone.utc)),
        ).fetchone()
        log_event(conn, row["id"], "建单", user["username"], herb, source, target)
        conn.commit()
    return row


@app.get("/api/transfers")
def list_transfers(status: str | None = None, _user: dict = Depends(current_user)):
    sql = """SELECT id, herb, source_factory, target_factory, status,
                    created_by, created_at, confirmed_by, confirmed_at
             FROM transfers"""
    params = ()
    if status and status.strip():
        if status not in ("pending", "effective"):
            raise HTTPException(status_code=400, detail="状态只能是 pending 或 effective")
        sql += " WHERE status = %s"
        params = (status,)
    sql += " ORDER BY id DESC"
    with connect() as conn:
        rows = conn.execute(sql, params).fetchall()
    return rows


@app.get("/api/transfers/events")
def list_transfer_events(_user: dict = Depends(current_user)):
    with connect() as conn:
        rows = conn.execute(
            """SELECT id, transfer_id, action, actor, detail, created_at
               FROM transfer_events ORDER BY id DESC"""
        ).fetchall()
    return rows


@app.post("/api/transfers/{transfer_id}/confirm")
def confirm_transfer(transfer_id: int, user: dict = Depends(require_writer)):
    with connect() as conn:
        row = conn.execute(
            "SELECT id, herb, source_factory, target_factory, status, created_by FROM transfers WHERE id = %s",
            (transfer_id,),
        ).fetchone()
        if row is None:
            raise HTTPException(status_code=404, detail="调拨单不存在")
        if row["status"] != "pending":
            raise HTTPException(status_code=409, detail="该调拨单已生效，不能重复确认")
        if row["created_by"] == user["username"]:
            raise HTTPException(status_code=403, detail="建单人不能自己确认，需另一名炮制员确认")
        now = datetime.now(timezone.utc)
        conn.execute(
            "UPDATE transfers SET status = 'effective', confirmed_by = %s, confirmed_at = %s WHERE id = %s",
            (user["username"], now, transfer_id),
        )
        # 生效后该味在源厂的最新一行划归目标厂，源厂按厂名筛即藏起该行
        conn.execute(
            """UPDATE batches SET factory = %s
               WHERE id = (
                   SELECT id FROM batches
                   WHERE herb = %s AND factory = %s
                   ORDER BY id DESC LIMIT 1
               )""",
            (row["target_factory"], row["herb"], row["source_factory"]),
        )
        log_event(conn, transfer_id, "确认", user["username"], row["herb"], row["source_factory"], row["target_factory"])
        conn.commit()
    return transfer_row(transfer_id)
