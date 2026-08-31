from fastapi import Depends, Header, HTTPException, Request, status
from .token import get_current_user, oauth2_scheme
from applications.user.models import User, UserRole

async def superuser_required(current_user: User = Depends(get_current_user)):
    if not current_user.role==UserRole.ADMIN:
        raise HTTPException(status_code=403, detail="Admin access required")
    return current_user


async def staff_required(current_user: User = Depends(get_current_user)):
    if not (current_user.role == UserRole.STAFF or current_user.role==UserRole.ADMIN):
        raise HTTPException(status_code=403, detail="Staff access required")
    return current_user


async def login_required(current_user: User = Depends(get_current_user)):
    return current_user


async def get_user(
    request: Request,
    token: str | None = Depends(oauth2_scheme),
    refresh_token: str | None = Header(default=None, alias="refresh-token"),
) -> User | None:
    try:
        current_user = await get_current_user(
            request=request,
            token=token,
            refresh_token=refresh_token,
        )
    except HTTPException as exc:
        if exc.status_code == status.HTTP_401_UNAUTHORIZED:
            return None
        raise

    if current_user is None:
        return None
    return current_user


def permission_required(codename: str):
    async def wrapper(current_user: User = Depends(get_current_user)):
        print("AUTH USER ID:", current_user.id)
        print("AUTH ROLE:", current_user.role, type(current_user.role))
        print("REQUIRED PERM:", codename)

        allowed = await current_user.has_permission(codename)
        print("HAS PERMISSION:", allowed)

        if not allowed:
            raise HTTPException(
                status_code=403,
                detail="Permission denied."
            )
        return current_user
    return wrapper


def role_required(*roles: UserRole, isGranted: bool = False):
    async def wrapper(
        current_user: User = Depends(get_current_user),
    ):
        if current_user.role == UserRole.ADMIN and isGranted:
            return current_user
        if current_user.role not in roles:
            # allowed = ", ".join(r.value for r in roles)
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Permission denied.",
            )
        return current_user
    return wrapper
