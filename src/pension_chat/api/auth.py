"""JWT-based authentication module.

Replaces Entra External ID with local OAuth2/JWT auth backed by the
employees table in Postgres. Supports:
- User registration (for pilot testing)
- Login with email/password → JWT token
- Token validation dependency for protected endpoints
- Optional auth dependency (some endpoints work anonymously)
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer, OAuth2PasswordRequestForm
from jose import JWTError, jwt
from passlib.context import CryptContext
from pydantic import BaseModel
from sqlalchemy import select

from pension_chat.config import get_settings
from pension_chat.db.session import get_admin_session

settings = get_settings()

# Password hashing
pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")

# OAuth2 scheme - tokenUrl points to our login endpoint
oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/api/v1/auth/login", auto_error=False)

router = APIRouter(prefix="/api/v1/auth", tags=["auth"])


# ── Schemas ───────────────────────────────────────────────────────

class UserRegister(BaseModel):
    email: str
    password: str


class UserLogin(BaseModel):
    email: str
    password: str


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    employee_id: str | None = None


class TokenData(BaseModel):
    employee_id: uuid.UUID
    email: str


# ── Helpers ───────────────────────────────────────────────────────

def verify_password(plain_password: str, hashed_password: str) -> bool:
    return pwd_context.verify(plain_password, hashed_password)


def hash_password(password: str) -> str:
    return pwd_context.hash(password)


def create_access_token(data: dict, expires_delta: timedelta | None = None) -> str:
    to_encode = data.copy()
    expire = datetime.now(UTC) + (
        expires_delta or timedelta(minutes=settings.jwt_access_token_expire_minutes)
    )
    to_encode.update({"exp": expire})
    return jwt.encode(to_encode, settings.jwt_secret_key, algorithm=settings.jwt_algorithm)


async def get_current_user(token: str | None = Depends(oauth2_scheme)) -> TokenData | None:
    """Decode and validate JWT token. Returns None if no token provided (anonymous)."""
    if token is None:
        return None

    try:
        payload = jwt.decode(
            token,
            settings.jwt_secret_key,
            algorithms=[settings.jwt_algorithm],
        )
        employee_id = payload.get("sub")
        email = payload.get("email")
        if employee_id is None or email is None:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid token",
                headers={"WWW-Authenticate": "Bearer"},
            )
        return TokenData(employee_id=uuid.UUID(employee_id), email=email)
    except JWTError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired token",
            headers={"WWW-Authenticate": "Bearer"},
        )


async def require_auth(user: TokenData | None = Depends(get_current_user)) -> TokenData:
    """Dependency that requires authentication."""
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authentication required",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return user


# ── Endpoints ─────────────────────────────────────────────────────

@router.post("/register", response_model=TokenResponse)
async def register(body: UserRegister):
    """Register a new user by setting password on an existing pre-seeded employee record.

    For the pilot, employees are pre-seeded with email but no password.
    Registration links the password to the employee record.
    """
    async with get_admin_session() as session:
        from pension_chat.db.models import Employee

        result = await session.execute(
            select(Employee).where(Employee.email == body.email)
        )
        employee = result.scalar_one_or_none()

        if employee is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="No pre-seeded employee record found for this email. "
                       "Contact your administrator to be added to the pilot.",
            )

        if employee.hashed_password is not None:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Account already registered. Please login.",
            )

        employee.hashed_password = hash_password(body.password)
        await session.commit()

        token = create_access_token(
            data={"sub": str(employee.id), "email": employee.email}
        )
        return TokenResponse(
            access_token=token,
            employee_id=str(employee.id),
        )


@router.post("/login", response_model=TokenResponse)
async def login(form_data: OAuth2PasswordRequestForm = Depends()):
    """Login with email/password and receive a JWT token."""
    async with get_admin_session() as session:
        from pension_chat.db.models import Employee

        result = await session.execute(
            select(Employee).where(Employee.email == form_data.username)
        )
        employee = result.scalar_one_or_none()

        if employee is None or employee.hashed_password is None:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid email or password",
                headers={"WWW-Authenticate": "Bearer"},
            )

        if not verify_password(form_data.password, employee.hashed_password):
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid email or password",
                headers={"WWW-Authenticate": "Bearer"},
            )

        token = create_access_token(
            data={"sub": str(employee.id), "email": employee.email}
        )
        return TokenResponse(
            access_token=token,
            employee_id=str(employee.id),
        )
