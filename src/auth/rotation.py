"""Automated GitHub Actions Secret Rotation using PyNaCl and GitHub REST API."""

import base64
import logging
import time
from typing import Optional
import httpx
from nacl import encoding, public

from src.config import settings

logger = logging.getLogger(__name__)


class GitHubSecretRotator:
    """Rotates repository secrets in GitHub Actions using libsodium public key encryption."""

    def __init__(
        self,
        repository: Optional[str] = None,
        pat: Optional[str] = None,
    ):
        self.repository = repository or settings.GITHUB_REPOSITORY
        self.pat = pat or settings.GH_PAT

    def _get_headers(self) -> dict:
        if not self.pat:
            raise ValueError("GH_PAT is required to update repository secrets.")
        return {
            "Authorization": f"Bearer {self.pat}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        }

    def get_public_key(self) -> dict:
        """Fetch repository public key used to encrypt secrets."""
        url = f"https://api.github.com/repos/{self.repository}/actions/secrets/public-key"
        with httpx.Client(timeout=15.0) as client:
            response = client.get(url, headers=self._get_headers())
            response.raise_for_status()
            return response.json()

    @staticmethod
    def encrypt_secret(public_key_b64: str, secret_value: str) -> str:
        """Encrypt secret using PyNaCl SealedBox and the repository's base64 public key."""
        public_key = public.PublicKey(public_key_b64.encode("utf-8"), encoding.Base64Encoder)
        sealed_box = public.SealedBox(public_key)
        encrypted = sealed_box.encrypt(secret_value.encode("utf-8"))
        return base64.b64encode(encrypted).decode("utf-8")

    def update_secret(
        self,
        secret_name: str,
        secret_value: str,
        max_retries: int = 3,
    ) -> bool:
        """Encrypt and update secret in GitHub Actions with retry logic."""
        if not self.repository or not self.pat:
            logger.warning(
                "Skipping GitHub Secret rotation: GITHUB_REPOSITORY or GH_PAT not configured."
            )
            return False

        logger.info("Starting rotation for secret '%s' in %s...", secret_name, self.repository)
        attempt = 0
        backoff = 2.0

        while attempt < max_retries:
            attempt += 1
            try:
                # 1. Fetch public key
                key_data = self.get_public_key()
                key_id = key_data["key_id"]
                key_b64 = key_data["key"]

                # 2. Encrypt value
                encrypted_value = self.encrypt_secret(key_b64, secret_value)

                # 3. PUT secret
                url = f"https://api.github.com/repos/{self.repository}/actions/secrets/{secret_name}"
                body = {
                    "encrypted_value": encrypted_value,
                    "key_id": key_id,
                }
                with httpx.Client(timeout=15.0) as client:
                    res = client.put(url, headers=self._get_headers(), json=body)
                    res.raise_for_status()

                logger.info(
                    "Successfully rotated GitHub Actions secret '%s' on attempt %d.",
                    secret_name,
                    attempt,
                )
                return True

            except Exception as e:
                logger.error(
                    "Attempt %d/%d failed to rotate secret '%s': %s",
                    attempt,
                    max_retries,
                    secret_name,
                    e,
                )
                if attempt < max_retries:
                    time.sleep(backoff)
                    backoff *= 2

        # Fallback emergency log
        logger.critical(
            "FAILED to rotate '%s' after %d retries. Immediate manual intervention required.",
            secret_name,
            max_retries,
        )
        return False
