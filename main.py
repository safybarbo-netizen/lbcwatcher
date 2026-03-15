"""
LBC Watcher SaaS — Backend FastAPI
Endpoints: auth, profils, scraping, stripe webhooks, admin
"""
from fastapi import FastAPI, HTTPException, Depends, Request, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, JSONResponse
from contextlib import asynccontextmanager
import asyncio, os, json, hashlib, secrets, time
from datetime import datetime, timedelta
from typing import Optional

from db import get_db, init_db, Database
from auth import hash_password, verify_password, create_token, decode_token
from scraper import fetch_listings
from stripe_webhooks import handle_stripe_event
from ws_manager import WebSocketManager

ws_manager = WebSocketManager()

@asynccontextmanager
async def lifespan(app: FastAPI):
    await init_db()
    asyncio.create_task(scraper_loop())
    yield

app = FastAPI(lifespan=lifespan)

app.add_middleware(CORSMiddleware,
    allow_origins=["*"], allow_credentials=True,
    allow_methods=["*"], allow_headers=["*"])

# ── AUTH ──────────────────────────────────────────────────────────────────────

@app.post("/api/register")
async def register(body: dict, db: Database = Depends(get_db)):
    email = body.get("email","").lower().strip()
    password = body.get("password","")
    if not email or not password or len(password) < 8:
        raise HTTPException(400, "Email et mot de passe requis (8 car. min.)")
    existing = await db.fetchrow("SELECT id FROM users WHERE email=$1", email)
    if existing:
        raise HTTPException(409, "Email déjà utilisé")
    pw_hash = hash_password(password)
    user_id = await db.fetchval(
        "INSERT INTO users(email,password_hash,plan,created_at) VALUES($1,$2,'free',NOW()) RETURNING id",
        email, pw_hash)
    token = create_token({"sub": str(user_id), "email": email})
    return {"token": token, "plan": "free", "email": email}

@app.post("/api/login")
async def login(body: dict, db: Database = Depends(get_db)):
    email = body.get("email","").lower().strip()
    password = body.get("password","")
    user = await db.fetchrow("SELECT * FROM users WHERE email=$1", email)
    if not user or not verify_password(password, user["password_hash"]):
        raise HTTPException(401, "Identifiants incorrects")
    token = create_token({"sub": str(user["id"]), "email": email})
    return {"token": token, "plan": user["plan"], "email": email}

@app.get("/api/me")
async def me(request: Request, db: Database = Depends(get_db)):
    user = await get_current_user(request, db)
    profiles = await db.fetch("SELECT * FROM profiles WHERE user_id=$1 ORDER BY created_at", user["id"])
    return {
        "id": user["id"],
        "email": user["email"],
        "plan": user["plan"],
        "stripe_customer_id": user.get("stripe_customer_id"),
        "profiles": [dict(p) for p in profiles],
        "profile_limit": get_profile_limit(user["plan"])
    }

# ── PROFILS ───────────────────────────────────────────────────────────────────

PLAN_LIMITS = {"free": 2, "starter": 5, "pro": 20, "business": 100}

def get_profile_limit(plan: str) -> int:
    return PLAN_LIMITS.get(plan, 2)

@app.post("/api/profiles")
async def create_profile(body: dict, request: Request, db: Database = Depends(get_db)):
    user = await get_current_user(request, db)
    count = await db.fetchval("SELECT COUNT(*) FROM profiles WHERE user_id=$1", user["id"])
    limit = get_profile_limit(user["plan"])
    if count >= limit:
        raise HTTPException(403, f"Limite de {limit} filtres pour le plan {user['plan']}")
    pid = await db.fetchval(
        """INSERT INTO profiles(user_id,name,filters,active,created_at)
           VALUES($1,$2,$3,false,NOW()) RETURNING id""",
        user["id"], body.get("name","Nouveau filtre"), json.dumps(body.get("filters",{})))
    return {"id": pid, "name": body["name"], "filters": body.get("filters",{}), "active": False}

@app.put("/api/profiles/{pid}")
async def update_profile(pid: int, body: dict, request: Request, db: Database = Depends(get_db)):
    user = await get_current_user(request, db)
    await db.execute(
        "UPDATE profiles SET name=$1,filters=$2 WHERE id=$3 AND user_id=$4",
        body.get("name"), json.dumps(body.get("filters",{})), pid, user["id"])
    return {"ok": True}

@app.delete("/api/profiles/{pid}")
async def delete_profile(pid: int, request: Request, db: Database = Depends(get_db)):
    user = await get_current_user(request, db)
    await db.execute("UPDATE profiles SET active=false WHERE id=$1 AND user_id=$2", pid, user["id"])
    await db.execute("DELETE FROM profiles WHERE id=$1 AND user_id=$2", pid, user["id"])
    return {"ok": True}

@app.post("/api/profiles/{pid}/toggle")
async def toggle_profile(pid: int, body: dict, request: Request, db: Database = Depends(get_db)):
    user = await get_current_user(request, db)
    active = body.get("active", False)
    await db.execute("UPDATE profiles SET active=$1 WHERE id=$2 AND user_id=$3", active, pid, user["id"])
    return {"ok": True, "active": active}

@app.get("/api/profiles/{pid}/results")
async def get_results(pid: int, request: Request, db: Database = Depends(get_db)):
    user = await get_current_user(request, db)
    profile = await db.fetchrow("SELECT * FROM profiles WHERE id=$1 AND user_id=$2", pid, user["id"])
    if not profile:
        raise HTTPException(404, "Profil introuvable")
    rows = await db.fetch(
        "SELECT * FROM listings WHERE profile_id=$1 ORDER BY found_at DESC LIMIT 50", pid)
    return {"listings": [dict(r) for r in rows]}

# ── STRIPE ────────────────────────────────────────────────────────────────────

import stripe as stripe_lib
stripe_lib.api_key = os.getenv("STRIPE_SECRET_KEY","")

PRICE_IDS = {
    "starter":  os.getenv("STRIPE_PRICE_STARTER","price_starter"),
    "pro":      os.getenv("STRIPE_PRICE_PRO","price_pro"),
    "business": os.getenv("STRIPE_PRICE_BUSINESS","price_business"),
}

@app.post("/api/stripe/checkout")
async def create_checkout(body: dict, request: Request, db: Database = Depends(get_db)):
    user = await get_current_user(request, db)
    plan = body.get("plan","starter")
    price_id = PRICE_IDS.get(plan)
    if not price_id:
        raise HTTPException(400, "Plan invalide")
    base_url = os.getenv("APP_URL","http://localhost:3000")
    session = stripe_lib.checkout.Session.create(
        customer_email=user["email"],
        payment_method_types=["card"],
        line_items=[{"price": price_id, "quantity": 1}],
        mode="subscription",
        success_url=f"{base_url}/dashboard?upgraded=1",
        cancel_url=f"{base_url}/pricing",
        metadata={"user_id": str(user["id"]), "plan": plan}
    )
    return {"url": session.url}

@app.post("/api/stripe/portal")
async def customer_portal(request: Request, db: Database = Depends(get_db)):
    user = await get_current_user(request, db)
    if not user.get("stripe_customer_id"):
        raise HTTPException(400, "Aucun abonnement actif")
    base_url = os.getenv("APP_URL","http://localhost:3000")
    session = stripe_lib.billing_portal.Session.create(
        customer=user["stripe_customer_id"],
        return_url=f"{base_url}/dashboard"
    )
    return {"url": session.url}

@app.post("/api/stripe/webhook")
async def stripe_webhook(request: Request, db: Database = Depends(get_db)):
    payload = await request.body()
    sig = request.headers.get("stripe-signature","")
    secret = os.getenv("STRIPE_WEBHOOK_SECRET","")
    try:
        event = stripe_lib.Webhook.construct_event(payload, sig, secret)
    except Exception as e:
        raise HTTPException(400, str(e))
    await handle_stripe_event(event, db)
    return {"ok": True}

# ── ADMIN ─────────────────────────────────────────────────────────────────────

async def require_admin(request: Request, db: Database):
    user = await get_current_user(request, db)
    if not user.get("is_admin"):
        raise HTTPException(403, "Accès refusé")
    return user

@app.get("/api/admin/users")
async def admin_users(request: Request, db: Database = Depends(get_db)):
    await require_admin(request, db)
    rows = await db.fetch("""
        SELECT u.id, u.email, u.plan, u.created_at, u.is_admin,
               COUNT(p.id) as profile_count
        FROM users u LEFT JOIN profiles p ON p.user_id=u.id
        GROUP BY u.id ORDER BY u.created_at DESC
    """)
    return {"users": [dict(r) for r in rows]}

@app.put("/api/admin/users/{uid}/plan")
async def admin_set_plan(uid: int, body: dict, request: Request, db: Database = Depends(get_db)):
    await require_admin(request, db)
    plan = body.get("plan","free")
    await db.execute("UPDATE users SET plan=$1 WHERE id=$2", plan, uid)
    return {"ok": True}

@app.delete("/api/admin/users/{uid}")
async def admin_delete_user(uid: int, request: Request, db: Database = Depends(get_db)):
    await require_admin(request, db)
    await db.execute("DELETE FROM profiles WHERE user_id=$1", uid)
    await db.execute("DELETE FROM users WHERE id=$1", uid)
    return {"ok": True}

@app.get("/api/admin/stats")
async def admin_stats(request: Request, db: Database = Depends(get_db)):
    await require_admin(request, db)
    total_users    = await db.fetchval("SELECT COUNT(*) FROM users")
    paid_users     = await db.fetchval("SELECT COUNT(*) FROM users WHERE plan != 'free'")
    active_profiles= await db.fetchval("SELECT COUNT(*) FROM profiles WHERE active=true")
    total_listings = await db.fetchval("SELECT COUNT(*) FROM listings")
    return {
        "total_users": total_users,
        "paid_users": paid_users,
        "active_profiles": active_profiles,
        "total_listings": total_listings,
    }

# ── WEBSOCKET ─────────────────────────────────────────────────────────────────

@app.websocket("/ws/{user_id}")
async def websocket_endpoint(websocket: WebSocket, user_id: str):
    await ws_manager.connect(websocket, user_id)
    try:
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        ws_manager.disconnect(user_id)

# ── SCRAPER LOOP ──────────────────────────────────────────────────────────────

async def scraper_loop():
    """Tourne en fond : vérifie les profils actifs et notifie via WebSocket."""
    while True:
        try:
            from db import get_db_direct
            db = await get_db_direct()
            profiles = await db.fetch("SELECT * FROM profiles WHERE active=true")
            for profile in profiles:
                filters = json.loads(profile["filters"] or "{}")
                listings, url, err = await asyncio.to_thread(fetch_listings, filters)
                if err or not listings:
                    continue
                for ad in listings:
                    exists = await db.fetchval(
                        "SELECT id FROM listings WHERE lbc_id=$1 AND profile_id=$2",
                        ad["id"], profile["id"])
                    if not exists and ad["id"]:
                        await db.execute("""
                            INSERT INTO listings(profile_id,lbc_id,title,price,location,url,attrs,found_at)
                            VALUES($1,$2,$3,$4,$5,$6,$7,NOW())
                        """, profile["id"], ad["id"], ad["title"], ad.get("price"),
                             ad.get("location"), ad.get("url"),
                             json.dumps(ad.get("attrs",{})))
                        await ws_manager.send_to_user(str(profile["user_id"]), {
                            "type": "new_listing",
                            "profile_id": profile["id"],
                            "listing": ad
                        })
            await db.close()
        except Exception as e:
            print(f"[scraper_loop] Erreur: {e}")
        await asyncio.sleep(30)

# ── HELPER AUTH ───────────────────────────────────────────────────────────────

async def get_current_user(request: Request, db: Database):
    auth = request.headers.get("Authorization","")
    if not auth.startswith("Bearer "):
        raise HTTPException(401, "Token manquant")
    token = auth[7:]
    payload = decode_token(token)
    if not payload:
        raise HTTPException(401, "Token invalide ou expiré")
    user = await db.fetchrow("SELECT * FROM users WHERE id=$1", int(payload["sub"]))
    if not user:
        raise HTTPException(401, "Utilisateur introuvable")
    return dict(user)

# ── SERVE FRONTEND ────────────────────────────────────────────────────────────

app.mount("/static", StaticFiles(directory="../frontend/static"), name="static")

@app.get("/{path:path}")
async def serve_frontend(path: str):
    pages = ["login","register","dashboard","pricing","admin"]
    page = path.split("/")[0]
    if page in pages:
        return FileResponse(f"../frontend/pages/{page}.html")
    return FileResponse("../frontend/pages/index.html")
