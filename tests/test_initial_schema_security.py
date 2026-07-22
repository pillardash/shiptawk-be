from pathlib import Path


def test_initial_schema_uses_encrypted_integration_connections_not_plaintext_social_accounts() -> (
    None
):
    migration = Path("alembic/versions/0001_initial_product_schema.py").read_text()

    assert '"integration_connections"' in migration
    assert '"credentials_ciphertext"' in migration
    assert '"social_accounts"' not in migration
    assert '"access_token"' not in migration
    assert '"refresh_token"' not in migration
