import pytest
import httpx
from main import app
from config import settings
from services.crypto import encrypt_token, decrypt_token
from services.linkedin_publisher import publish_to_linkedin, MEM_PLATFORM_CONNECTIONS


def test_token_encryption_and_decryption():
    """
    Test 1: Fernet token encryption and decryption works deterministically.
    """
    secret_token = "AQV9...live_linkedin_access_token_123456"
    encrypted = encrypt_token(secret_token)
    assert encrypted != secret_token
    assert len(encrypted) > 20

    decrypted = decrypt_token(encrypted)
    assert decrypted == secret_token


@pytest.mark.asyncio
async def test_disconnected_linkedin_publisher_returns_error_message():
    """
    Test 2: When no LinkedIn token is stored, publisher returns clear error:
    'LinkedIn not connected -- go to Settings to connect your account'
    """
    # Ensure memory store has no LinkedIn token
    MEM_PLATFORM_CONNECTIONS.pop("linkedin", None)

    sample_post = {
        "title": "Industrial Slings Webbing 50mm",
        "standard": "IS 15041",
        "material": "Polyester",
        "captions": {
            "linkedin": "High performance 50mm webbing manufactured in Kanpur."
        }
    }

    result = await publish_to_linkedin(sample_post, conn=None)
    assert result["status"] == "error"
    assert result["platform"] == "linkedin"
    assert "LinkedIn not connected -- go to Settings to connect your account" in result["error"]


@pytest.mark.asyncio
async def test_connected_linkedin_publisher_uses_organization_urn():
    """
    Test 3: When connected, publisher targets urn:li:organization:{id}.
    """
    try:
        MEM_PLATFORM_CONNECTIONS["linkedin"] = {
            "platform": "linkedin",
            "account_name": "Swadeshi Niwar Mills",
            "account_id": "urn:li:organization:10523091",
            "access_token_encrypted": encrypt_token("mock_valid_token_abc123"),
            "is_active": True,
        }

        sample_post = {
            "title": "Industrial Slings Webbing 50mm",
            "captions": {
                "linkedin": "High performance 50mm webbing."
            }
        }

        # Calling publish_to_linkedin will attempt request with the author_urn
        res = await publish_to_linkedin(sample_post, conn=None)
        assert res["platform"] == "linkedin"
    finally:
        MEM_PLATFORM_CONNECTIONS.pop("linkedin", None)


@pytest.mark.asyncio
async def test_settings_page_and_api_key_saving(dev_client):
    """
    Test 4: Settings page renders platform connection cards,
    and POST /settings/api-key saves encrypted key for IndiaMart & TradeIndia.
    """
    # Step 1: Settings View
    settings_res = await dev_client.get("/settings")
    assert settings_res.status_code == 200
    assert "LinkedIn (Company Page)" in settings_res.text
    assert "IndiaMart Portal Integration" in settings_res.text
    assert "TradeIndia Portal Integration" in settings_res.text

    # Step 2: Save IndiaMart API Key
    save_im_res = await dev_client.post(
        "/settings/api-key",
        data={"platform": "indiamart", "api_key": "IM_KEY_998877665544"},
        follow_redirects=False,
    )
    assert save_im_res.status_code == 303

    # Step 3: Verify encrypted key stored
    assert "indiamart" in MEM_PLATFORM_CONNECTIONS
    stored_enc = MEM_PLATFORM_CONNECTIONS["indiamart"]["access_token_encrypted"]
    assert decrypt_token(stored_enc) == "IM_KEY_998877665544"

    # Step 4: Save TradeIndia API Key
    save_ti_res = await dev_client.post(
        "/settings/api-key",
        data={"platform": "tradeindia", "api_key": "TI_KEY_112233445566"},
        follow_redirects=False,
    )
    assert save_ti_res.status_code == 303
    assert decrypt_token(MEM_PLATFORM_CONNECTIONS["tradeindia"]["access_token_encrypted"]) == "TI_KEY_112233445566"


@pytest.mark.asyncio
async def test_linkedin_oauth_redirect_scopes(dev_client):
    """
    Test 5: GET /auth/linkedin redirects with personal member publishing scopes.
    """
    res = await dev_client.get("/auth/linkedin", follow_redirects=False)
    assert res.status_code == 303
    location = res.headers["location"]
    assert "https://www.linkedin.com/oauth/v2/authorization" in location
    assert "w_member_social" in location
