import bcrypt, jwt, os
from datetime import datetime, timedelta

SECRET = os.getenv("JWT_SECRET", "change-me-in-production-please")
ALGO   = "HS256"
EXPIRE = 30  # jours

def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()

def verify_password(password: str, hashed: str) -> bool:
    return bcrypt.checkpw(password.encode(), hashed.encode())

def create_token(payload: dict) -> str:
    data = {**payload, "exp": datetime.utcnow() + timedelta(days=EXPIRE)}
    return jwt.encode(data, SECRET, algorithm=ALGO)

def decode_token(token: str) -> dict | None:
    try:
        return jwt.decode(token, SECRET, algorithms=[ALGO])
    except Exception:
        return None
