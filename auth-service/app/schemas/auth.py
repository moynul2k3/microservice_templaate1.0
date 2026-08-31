from datetime import datetime
from typing import Literal

from fastapi import Form
from pydantic import AliasChoices, BaseModel, ConfigDict, EmailStr, Field

OtpPurpose = Literal["signup", "login"]


class UserCreate(BaseModel):
    email: EmailStr
    password: str = Field(min_length=3, max_length=128)
    is_2fa: bool = False


class SendOtpRequest(BaseModel):
    email: EmailStr = Field(
        validation_alias=AliasChoices("email", "gmail"),
        serialization_alias="email",
    )
    purpose: OtpPurpose

    @classmethod
    def as_form(
        cls,
        email: EmailStr = Form(...),
        purpose: OtpPurpose = Form(...),
    ) -> "SendOtpRequest":
        return cls(email=email, purpose=purpose)


class UserSignup(BaseModel):
    email: EmailStr = Field(
        validation_alias=AliasChoices("email", "gmail"),
        serialization_alias="email",
    )
    purpose: OtpPurpose
    otp: str = Field(
        min_length=6,
        max_length=6,
        pattern=r"^\d{6}$",
        validation_alias=AliasChoices("otp", "OTP"),
        serialization_alias="otp",
    )
    password: str = Field(min_length=3, max_length=128)
    is_2fa: bool = False

    @classmethod
    def as_form(
        cls,
        email: EmailStr = Form(...),
        purpose: OtpPurpose = Form(...),
        otp: str = Form(...),
        password: str = Form(...),
        is_2fa: bool = Form(False),
    ) -> "UserSignup":
        return cls(
            email=email,
            purpose=purpose,
            otp=otp,
            password=password,
            is_2fa=is_2fa,
        )


class UserLogin(BaseModel):
    email: EmailStr
    password: str
    otp: str | None = Field(
        default=None,
        min_length=6,
        max_length=6,
        pattern=r"^\d{6}$",
    )

    @classmethod
    def as_form(
        cls,
        email: EmailStr = Form(...),
        password: str = Form(...),
        otp: str | None = Form(default=None),
    ) -> "UserLogin":
        return cls(email=email, password=password, otp=otp)


class UserRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    email: EmailStr
    is_2fa: bool
    is_active: bool
    is_superuser: bool
    created_at: datetime
    updated_at: datetime
    last_login_at: datetime | None = None


class TokenResponse(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"
    user: UserRead


class TokenVerifyResponse(BaseModel):
    valid: bool = True
    user: UserRead


class MessageResponse(BaseModel):
    detail: str


class LoginOtpRequiredResponse(BaseModel):
    detail: str
    otp_required: bool = True
