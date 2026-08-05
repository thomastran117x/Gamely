from pydantic import BaseModel, Field

PASSWORD_MAX_LENGTH = 128


class CredentialsRequest(BaseModel):
    email: str = Field(max_length=320)
    password: str = Field(max_length=PASSWORD_MAX_LENGTH)


class EmailRequest(BaseModel):
    email: str = Field(max_length=320)


class VerifyEmailRequest(EmailRequest):
    code: str = Field(pattern=r"^\d{6}$")


class ResetPasswordRequest(VerifyEmailRequest):
    password: str = Field(max_length=PASSWORD_MAX_LENGTH)


class ChangePasswordRequest(BaseModel):
    current_password: str = Field(max_length=PASSWORD_MAX_LENGTH)
    new_password: str = Field(max_length=PASSWORD_MAX_LENGTH)


class OAuthTokenRequest(BaseModel):
    id_token: str = Field(max_length=16384)
    nonce: str = Field(max_length=256)


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
