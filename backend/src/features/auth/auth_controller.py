from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, Request, Response

from src.application.environment.environment_manager import Settings
from src.features.auth.auth_dto import ChangePasswordRequest, CredentialsRequest, EmailRequest, OAuthTokenRequest, ResetPasswordRequest, TokenResponse, VerifyEmailRequest
from src.features.auth.auth_service import AuthService
from src.features.auth.oauth.oauth_service import OAuthService
from src.features.auth.token.token_service import TokenService
from src.shared.exceptions import UnauthorizedError

router = APIRouter(prefix="/auth", tags=["auth"])


class AuthController:
    def __init__(self, auth: AuthService, oauth: OAuthService, tokens: TokenService, settings: Settings) -> None:
        self._auth = auth
        self._oauth = oauth
        self._tokens = tokens
        self._settings = settings

    async def signup(self, body: CredentialsRequest) -> dict[str, str]:
        await self._auth.signup(body.email, body.password)
        return {"status": "verification_sent"}

    async def login(self, body: CredentialsRequest, response: Response) -> TokenResponse:
        _, access, refresh = await self._auth.login(body.email, body.password)
        self._set_refresh(response, refresh)
        return TokenResponse(access_token=access)

    async def verify_email(self, body: VerifyEmailRequest) -> dict[str, str]:
        await self._auth.verify_email(body.email, body.code)
        return {"status": "verified"}

    async def resend_verification(self, body: EmailRequest) -> dict[str, str]:
        await self._auth.send_verification(body.email)
        return {"status": "verification_sent"}

    async def forgot_password(self, body: EmailRequest) -> dict[str, str]:
        await self._auth.forgot_password(body.email)
        return {"status": "reset_sent"}

    async def reset_password(self, body: ResetPasswordRequest) -> dict[str, str]:
        await self._auth.reset_password(body.email, body.code, body.password)
        return {"status": "password_reset"}

    async def change_password(self, body: ChangePasswordRequest, request: Request, response: Response) -> dict[str, str]:
        await self._auth.change_password(self._current_user(request), body.current_password, body.new_password)
        self._clear_refresh(response)
        return {"status": "password_changed"}

    async def refresh(self, request: Request, response: Response) -> TokenResponse:
        value = request.cookies.get(self._settings.auth_refresh_cookie_name)
        if not value:
            raise UnauthorizedError("A refresh token is required.")
        _, access, refresh = await self._auth.refresh(value)
        self._set_refresh(response, refresh)
        return TokenResponse(access_token=access)

    async def logout(self, request: Request, response: Response) -> Response:
        value = request.cookies.get(self._settings.auth_refresh_cookie_name)
        if value:
            await self._tokens.revoke_refresh(value)
        self._clear_refresh(response)
        return response

    async def logout_all(self, request: Request, response: Response) -> Response:
        await self._tokens.revoke_all(self._current_user(request))
        self._clear_refresh(response)
        return response

    async def me(self, request: Request) -> dict[str, object]:
        user = await self._auth._require_user(self._current_user(request))
        return {"id": str(user.id), "email": user.email, "email_verified": user.email_verified_at is not None}

    async def oauth_challenge(self, provider: str) -> dict[str, str]:
        return {"nonce": await self._oauth.create_challenge(provider)}

    async def oauth_login(self, provider: str, body: OAuthTokenRequest, response: Response) -> TokenResponse:
        _, access, refresh = await self._oauth.login(provider, body.id_token, body.nonce)
        self._set_refresh(response, refresh)
        return TokenResponse(access_token=access)

    def _current_user(self, request: Request) -> UUID:
        header = request.headers.get("Authorization", "")
        if not header.startswith("Bearer "):
            raise UnauthorizedError()
        return self._tokens.decode_access(header[7:])

    def _set_refresh(self, response: Response, value: str) -> None:
        response.set_cookie(key=self._settings.auth_refresh_cookie_name, value=value, httponly=True, secure=self._settings.auth_refresh_cookie_secure, samesite=self._settings.auth_refresh_cookie_samesite, domain=self._settings.auth_refresh_cookie_domain, path=self._settings.auth_refresh_cookie_path, max_age=self._settings.auth_refresh_token_days * 86400)

    def _clear_refresh(self, response: Response) -> None:
        response.delete_cookie(key=self._settings.auth_refresh_cookie_name, domain=self._settings.auth_refresh_cookie_domain, path=self._settings.auth_refresh_cookie_path)


def get_controller(request: Request) -> AuthController:
    return request.state.service_scope.get(AuthController)


@router.post("/signup", status_code=202)
async def signup(body: CredentialsRequest, controller: AuthController = Depends(get_controller)) -> dict[str, str]:
    return await controller.signup(body)


@router.post("/login", response_model=TokenResponse)
async def login(body: CredentialsRequest, response: Response, controller: AuthController = Depends(get_controller)) -> TokenResponse:
    return await controller.login(body, response)


@router.post("/verify-email")
async def verify_email(body: VerifyEmailRequest, controller: AuthController = Depends(get_controller)) -> dict[str, str]:
    return await controller.verify_email(body)


@router.post("/resend-verification", status_code=202)
async def resend_verification(body: EmailRequest, controller: AuthController = Depends(get_controller)) -> dict[str, str]:
    return await controller.resend_verification(body)


@router.post("/forgot-password", status_code=202)
async def forgot_password(body: EmailRequest, controller: AuthController = Depends(get_controller)) -> dict[str, str]:
    return await controller.forgot_password(body)


@router.post("/reset-password")
async def reset_password(body: ResetPasswordRequest, controller: AuthController = Depends(get_controller)) -> dict[str, str]:
    return await controller.reset_password(body)


@router.post("/change-password")
async def change_password(body: ChangePasswordRequest, request: Request, response: Response, controller: AuthController = Depends(get_controller)) -> dict[str, str]:
    return await controller.change_password(body, request, response)


@router.post("/refresh", response_model=TokenResponse)
async def refresh(request: Request, response: Response, controller: AuthController = Depends(get_controller)) -> TokenResponse:
    return await controller.refresh(request, response)


@router.post("/logout", status_code=204)
async def logout(request: Request, response: Response, controller: AuthController = Depends(get_controller)) -> Response:
    return await controller.logout(request, response)


@router.post("/logout-all", status_code=204)
async def logout_all(request: Request, response: Response, controller: AuthController = Depends(get_controller)) -> Response:
    return await controller.logout_all(request, response)


@router.get("/me")
async def me(request: Request, controller: AuthController = Depends(get_controller)) -> dict[str, object]:
    return await controller.me(request)


@router.post("/oauth/{provider}/challenge")
async def oauth_challenge(provider: str, controller: AuthController = Depends(get_controller)) -> dict[str, str]:
    return await controller.oauth_challenge(provider)


@router.post("/oauth/{provider}", response_model=TokenResponse)
async def oauth_login(provider: str, body: OAuthTokenRequest, response: Response, controller: AuthController = Depends(get_controller)) -> TokenResponse:
    return await controller.oauth_login(provider, body, response)