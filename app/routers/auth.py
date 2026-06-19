import urllib.parse

import httpx
from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import RedirectResponse
from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.database import get_db
from app.deps import get_current_user
from app.models import User, Workspace
from app.schemas import AuthConfigOut, LoginRequest, TokenOut, UserOut
from app.security import create_access_token, verify_password
from app.seed import seed_default_categories

router = APIRouter(prefix="/api/auth", tags=["auth"])

GOOGLE_AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
GOOGLE_TOKEN_URL = "https://oauth2.googleapis.com/token"
GOOGLE_USERINFO_URL = "https://www.googleapis.com/oauth2/v3/userinfo"


@router.get("/config", response_model=AuthConfigOut)
async def auth_config():
    return AuthConfigOut(
        google_enabled=settings.google_enabled,
        ai_enabled=settings.ai_enabled,
        default_academic_year=settings.default_academic_year,
    )


@router.post("/login", response_model=TokenOut)
async def login(body: LoginRequest, db: AsyncSession = Depends(get_db)):
    ident = body.identifier.strip().lower()
    result = await db.execute(
        select(User).where(or_(User.email == ident, User.username == ident))
    )
    user = result.scalar_one_or_none()
    if user is None or not user.password_hash or not verify_password(body.password, user.password_hash):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid credentials"
        )
    token = create_access_token(
        user_id=str(user.id), workspace_id=str(user.workspace_id), role=user.role
    )
    return TokenOut(access_token=token, user=UserOut.model_validate(user))


@router.get("/me", response_model=UserOut)
async def me(user: User = Depends(get_current_user)):
    return UserOut.model_validate(user)


@router.get("/google/login")
async def google_login():
    if not settings.google_enabled:
        raise HTTPException(status_code=400, detail="Google OAuth is not configured")
    params = {
        "client_id": settings.google_client_id,
        "redirect_uri": settings.google_redirect_uri,
        "response_type": "code",
        "scope": "openid email profile",
        "access_type": "offline",
        "prompt": "select_account",
    }
    return RedirectResponse(f"{GOOGLE_AUTH_URL}?{urllib.parse.urlencode(params)}")


@router.get("/google/callback")
async def google_callback(code: str | None = None, error: str | None = None,
                          db: AsyncSession = Depends(get_db)):
    if error or not code:
        return RedirectResponse(f"{settings.frontend_url}/login?error=google_failed")

    async with httpx.AsyncClient(timeout=20) as client:
        token_resp = await client.post(
            GOOGLE_TOKEN_URL,
            data={
                "code": code,
                "client_id": settings.google_client_id,
                "client_secret": settings.google_client_secret,
                "redirect_uri": settings.google_redirect_uri,
                "grant_type": "authorization_code",
            },
        )
        if token_resp.status_code != 200:
            return RedirectResponse(f"{settings.frontend_url}/login?error=token_exchange")
        access_token = token_resp.json().get("access_token")
        info_resp = await client.get(
            GOOGLE_USERINFO_URL, headers={"Authorization": f"Bearer {access_token}"}
        )
        if info_resp.status_code != 200:
            return RedirectResponse(f"{settings.frontend_url}/login?error=userinfo")
        info = info_resp.json()

    sub = info.get("sub")
    email = (info.get("email") or "").lower()
    name = info.get("name") or email.split("@")[0]

    result = await db.execute(select(User).where(User.google_sub == sub))
    user = result.scalar_one_or_none()
    if user is None:
        result = await db.execute(select(User).where(User.email == email))
        user = result.scalar_one_or_none()

    if user is None:
        # New Google user -> fresh workspace
        ws = Workspace(name=f"{name}'s Workspace")
        db.add(ws)
        await db.flush()
        await seed_default_categories(db, ws.id)
        user = User(
            workspace_id=ws.id,
            name=name,
            email=email,
            google_sub=sub,
            auth_provider="google",
            role="superadmin",
        )
        db.add(user)
        await db.flush()
    elif not user.google_sub:
        user.google_sub = sub
        user.auth_provider = user.auth_provider or "google"

    await db.commit()
    await db.refresh(user)

    token = create_access_token(
        user_id=str(user.id), workspace_id=str(user.workspace_id), role=user.role
    )
    return RedirectResponse(f"{settings.frontend_url}/auth/callback?token={token}")
