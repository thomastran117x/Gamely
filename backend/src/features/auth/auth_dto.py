from pydantic import BaseModel, Field


class CredentialsRequest(BaseModel):
    email: str
    password: str


class EmailRequest(BaseModel):
    email: str


class VerifyEmailRequest(EmailRequest):
    code: str = Field(pattern=r"^\d{6}$")


class ResetPasswordRequest(VerifyEmailRequest):
    password: str


class ChangePasswordRequest(BaseModel):
    current_password: str
    new_password: str


class OAuthTokenRequest(BaseModel):
    id_token: str
    nonce: str


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
