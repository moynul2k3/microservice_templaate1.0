from typing import Annotated

from fastapi import APIRouter, Cookie, Depends, Header, HTTPException, Query, Response, status
from jose import JWTError
from tortoise.exceptions import IntegrityError

from app.core.config import settings
from app.core.security import (
    ACCESS_COOKIE_NAME,
    REFRESH_COOKIE_NAME,
    create_access_token,
    create_refresh_token,
    decode_access_token,
    decode_refresh_token,
)
from app.models.user import User
from app.schemas.auth import (
    LoginOtpRequiredResponse,
    MessageResponse,
    SendOtpRequest,
    TokenResponse,
    TokenVerifyResponse,
    UserLogin,
    UserRead,
    UserSignup,
)
from app.services.auth_service import create_user, get_user_by_email, mark_login_success, verify_user_credentials
from app.services.otp_service import send_otp, verify_otp

router = APIRouter(prefix="/auth", tags=["auth"])


def _cookies_secure() -> bool:
    return not settings.DEBUG


def _set_auth_cookies(response: Response, access_token: str, refresh_token: str) -> None:
    response.set_cookie(
        key=ACCESS_COOKIE_NAME,
        value=access_token,
        httponly=True,
        secure=_cookies_secure(),
        samesite="lax",
        max_age=settings.JWT_ACCESS_TOKEN_EXPIRE_MINUTES * 60,
        path="/",
    )
    response.set_cookie(
        key=REFRESH_COOKIE_NAME,
        value=refresh_token,
        httponly=True,
        secure=_cookies_secure(),
        samesite="lax",
        max_age=settings.JWT_REFRESH_TOKEN_EXPIRE_DAYS * 24 * 60 * 60,
        path="/",
    )


def _token_response(response: Response, user: User) -> TokenResponse:
    access_token = create_access_token(str(user.id))
    refresh_token = create_refresh_token(str(user.id))
    _set_auth_cookies(response, access_token, refresh_token)
    return TokenResponse(
        access_token=access_token,
        refresh_token=refresh_token,
        user=UserRead.model_validate(user),
    )


def _get_token_from_header_or_cookie(authorization: str | None, cookie_token: str | None) -> str:
    if authorization:
        if not authorization.lower().startswith("bearer "):
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid auth header")
        token = authorization.split(" ", 1)[1].strip()
        if token:
            return token
    if cookie_token:
        return cookie_token
    raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Not authenticated")


async def _get_user_from_token(token: str) -> User:
    try:
        payload = decode_access_token(token)
        user_id = int(payload["sub"])
    except (JWTError, KeyError, TypeError, ValueError):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token")

    user = await User.get_or_none(id=user_id)
    if not user or not user.is_active:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="User not found")
    return user


async def _current_user(authorization: str | None, access_token: str | None) -> User:
    token = _get_token_from_header_or_cookie(authorization, access_token)
    return await _get_user_from_token(token)


def _get_refresh_token_from_header_or_cookie(refresh_token: str | None, cookie_token: str | None) -> str:
    if refresh_token:
        if refresh_token.lower().startswith("bearer "):
            token = refresh_token.split(" ", 1)[1].strip()
        else:
            token = refresh_token.strip()
        if token:
            return token
    if cookie_token:
        return cookie_token
    raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Refresh token required")


async def _complete_signup(payload: UserSignup, response: Response) -> TokenResponse:
    if payload.purpose != "signup":
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid OTP purpose for signup")
    exists = await get_user_by_email(payload.email)
    if exists:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Email already in use")
    await verify_otp(payload.email, payload.purpose, payload.otp)
    try:
        user = await create_user(payload)
    except IntegrityError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Email already in use") from exc
    return _token_response(response, user)


@router.post("/send-otp", response_model=MessageResponse)
async def send_signup_otp(payload: Annotated[SendOtpRequest, Depends(SendOtpRequest.as_form)]):
    await send_otp(payload.email, payload.purpose)
    return MessageResponse(detail="OTP sent successfully")


@router.post("/signup", response_model=TokenResponse, status_code=status.HTTP_201_CREATED)
async def signup(payload: Annotated[UserSignup, Depends(UserSignup.as_form)], response: Response):
    return await _complete_signup(payload, response)


@router.post("/register", response_model=TokenResponse, status_code=status.HTTP_201_CREATED, include_in_schema=False)
async def register(payload: Annotated[UserSignup, Depends(UserSignup.as_form)], response: Response):
    return await _complete_signup(payload, response)


@router.post(
    "/login",
    response_model=TokenResponse,
    responses={202: {"model": LoginOtpRequiredResponse}},
)
async def login(payload: Annotated[UserLogin, Depends(UserLogin.as_form)], response: Response):
    user = await verify_user_credentials(payload.email, payload.password)
    if not user:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid credentials")

    if user.is_2fa:
        if not payload.otp:
            await send_otp(payload.email, "login")
            response.status_code = status.HTTP_202_ACCEPTED
            return LoginOtpRequiredResponse(
                detail="OTP sent successfully. Submit the login form again with the otp field to complete sign in."
            )
        await verify_otp(payload.email, "login", payload.otp)

    await mark_login_success(user)
    return _token_response(response, user)


@router.post("/refresh", response_model=TokenResponse)
async def refresh(
    response: Response,
    refresh_token_query: str | None = Query(default=None, alias="refresh_token"),
    refresh_token: str | None = Header(default=None, alias="refresh-token"),
    refresh_cookie: str | None = Cookie(default=None, alias=REFRESH_COOKIE_NAME),
):
    token = _get_refresh_token_from_header_or_cookie(refresh_token_query or refresh_token, refresh_cookie)
    try:
        payload = decode_refresh_token(token)
        user_id = int(payload["sub"])
    except (JWTError, KeyError, TypeError, ValueError):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid refresh token")

    user = await User.get_or_none(id=user_id)
    if not user or not user.is_active:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="User not found")
    return _token_response(response, user)


@router.get("/verify", response_model=TokenVerifyResponse)
async def verify_token(
    authorization: str | None = Header(default=None),
    access_token_query: str | None = Query(default=None, alias="access_token"),
    access_token: str | None = Cookie(default=None),
):
    user = await _current_user(authorization, access_token_query or access_token)
    return TokenVerifyResponse(user=UserRead.model_validate(user))


@router.get("/me", response_model=UserRead)
async def me(
    authorization: str | None = Header(default=None),
    access_token_query: str | None = Query(default=None, alias="access_token"),
    access_token: str | None = Cookie(default=None),
):
    user = await _current_user(authorization, access_token_query or access_token)
    return UserRead.model_validate(user)


@router.get("/swagger-auth-token/", response_model=TokenResponse)
async def swagger_auth_token(
    response: Response,
    access_token: str | None = Cookie(default=None),
    authorization: str | None = Header(default=None),
):
    user = await _current_user(authorization, access_token)
    return _token_response(response, user)


def _delete_auth_cookies(response: Response) -> None:
    response.delete_cookie(ACCESS_COOKIE_NAME, path="/")
    response.delete_cookie(REFRESH_COOKIE_NAME, path="/")


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
def logout(response: Response):
    _delete_auth_cookies(response)


@router.post("/logout/", status_code=status.HTTP_204_NO_CONTENT, include_in_schema=False)
def logout_slash(response: Response):
    _delete_auth_cookies(response)
