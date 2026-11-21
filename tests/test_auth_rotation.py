"""Tests for GitHub secret encryption and token rotation logic."""

import base64
import pytest
from nacl import public

from src.auth.rotation import GitHubSecretRotator


def test_pynacl_secret_encryption():
    """Verify that PyNaCl sealed box encrypts a secret with a public key and can be decrypted."""
    # Generate test keypair
    private_key = public.PrivateKey.generate()
    public_key_b64 = base64.b64encode(bytes(private_key.public_key)).decode("utf-8")

    secret_message = "test_caliber_refresh_token_xyz"
    encrypted_b64 = GitHubSecretRotator.encrypt_secret(public_key_b64, secret_message)

    # Decrypt with private key to verify authenticity
    encrypted_bytes = base64.b64decode(encrypted_b64)
    unseal_box = public.SealedBox(private_key)
    decrypted = unseal_box.decrypt(encrypted_bytes).decode("utf-8")

    assert decrypted == secret_message


def test_secret_rotator_requires_credentials():
    """Rotator should skip or raise when required credentials are missing."""
    rotator = GitHubSecretRotator(repository="", pat="")
    # Should safely return False without crashing
    assert rotator.update_secret("TEST_SECRET", "dummy_val") is False
