"""
Vercel Serverless entrypoint for Pocketpay (Stage 4).
Exposes ASGI application for Vercel Serverless runtime.
"""
from __future__ import annotations

import os
import sys
import shutil
import urllib.parse

# 1. Resolve project paths so app and models can be imported cleanly
CURRENT_DIR = os.path.dirname(os.path.abspath(__file__))
ROOT_DIR = os.path.dirname(CURRENT_DIR)
STAGE4_DIR = os.path.join(ROOT_DIR, "stage-4")

if STAGE4_DIR not in sys.path:
    sys.path.insert(0, STAGE4_DIR)
if ROOT_DIR not in sys.path:
    sys.path.insert(0, ROOT_DIR)

# 2. Database path in /tmp for Vercel Serverless environment (filesystem is read-only except /tmp)
if os.name == "nt":
    target_db = os.getenv("POCKETFUL_DB_PATH", os.path.join(STAGE4_DIR, "pocketful_stage4.db"))
else:
    target_db = os.getenv("POCKETFUL_DB_PATH", "/tmp/pocketful_stage4.db")

os.environ["POCKETFUL_DB_PATH"] = target_db

# 3. Seed /tmp database from stage-4 template if missing
src_db = os.path.join(STAGE4_DIR, "pocketful_stage4.db")
if target_db != src_db and not os.path.exists(target_db):
    if os.path.exists(src_db):
        try:
            shutil.copyfile(src_db, target_db)
        except Exception as e:
            print(f"[Vercel Init] Failed to copy seed DB: {e}")

# 4. Import FastAPI application and ledger
from fastapi import Request
from starlette.types import ASGIApp, Scope, Receive, Send
from app.main import app, ledger
from app.models import hash_password

# 5. ASGI Middleware to handle Vercel serverless rewrites and path resolution
class VercelPathFixMiddleware:
    def __init__(self, app: ASGIApp):
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send):
        if scope["type"] == "http":
            raw_headers = scope.get("headers", [])
            query_bytes = scope.get("query_string", b"")
            query_string = query_bytes.decode("latin1", errors="ignore")
            
            orig_path = None
            if "__path__=" in query_string:
                parts = query_string.split("&")
                remaining = []
                for part in parts:
                    if part.startswith("__path__="):
                        val = part.split("=", 1)[1]
                        orig_path = urllib.parse.unquote(val)
                    else:
                        remaining.append(part)
                # Strip internal __path__ param from query string for route handler
                clean_qs = "&".join(remaining)
                scope["query_string"] = clean_qs.encode("latin1")
            
            if not orig_path:
                for hdr_key, hdr_val in raw_headers:
                    k = hdr_key.decode("latin1").lower()
                    if k in ("x-forwarded-uri", "x-matched-path", "x-vercel-sc-path", "x-original-url"):
                        v = hdr_val.decode("latin1", errors="ignore")
                        if v and not v.startswith("/api/index.py"):
                            orig_path = v.split("?")[0]
                            break
            
            if orig_path:
                if not orig_path.startswith("/"):
                    orig_path = "/" + orig_path
                scope["path"] = orig_path
                scope["raw_path"] = orig_path.encode("latin1")
            elif scope.get("path", "").startswith("/api/index.py"):
                sub = scope["path"][len("/api/index.py"):]
                if not sub or sub == "/":
                    scope["path"] = "/"
                else:
                    scope["path"] = sub if sub.startswith("/") else ("/" + sub)
                scope["raw_path"] = scope["path"].encode("latin1")

        await self.app(scope, receive, send)

app.add_middleware(VercelPathFixMiddleware)

@app.get("/_debug_headers")
def debug_headers(request: Request):
    return {
        "url": str(request.url),
        "path": request.url.path,
        "headers": dict(request.headers),
        "scope_path": request.scope.get("path"),
    }

# 6. Ensure demo accounts (Alice, Bob, Carol) are pre-seeded and verified
try:
    with ledger._lock:
        conn = ledger._get_connection()
        cur = conn.cursor()
        demo_users = [
            ("user_alice_demo", "alice@demo.com", hash_password("Password123!"), "Alice Demo", "alice", 150000, 150000),
            ("user_bob_demo", "bob@demo.com", hash_password("Password123!"), "Bob Demo", "bob", 85000, 85000),
            ("user_carol_demo", "carol@demo.com", hash_password("Password123!"), "Carol Demo", "carol", 50000, 50000),
        ]
        for uid, email, pwhash, dname, handle, bal, open_bal in demo_users:
            row = cur.execute("SELECT id FROM users WHERE email = ?", (email,)).fetchone()
            if not row:
                cur.execute(
                    "INSERT INTO users (id, email, password_hash, display_name, handle, balance, opening_balance) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (uid, email, pwhash, dname, handle, bal, open_bal)
                )
            else:
                cur.execute("UPDATE users SET password_hash = ? WHERE email = ?", (pwhash, email))
        conn.commit()
except Exception as e:
    print(f"[Vercel Init] Demo accounts initialization notice: {e}")
