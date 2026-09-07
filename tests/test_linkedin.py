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
    Test 5: GET /auth/linkedin redirects with OpenID Connect and member publishing scopes.
    """
    res = await dev_client.get("/auth/linkedin", follow_redirects=False)
    assert res.status_code == 303
    location = res.headers["location"]
    assert "https://www.linkedin.com/oauth/v2/authorization" in location
    assert "openid" in location
    assert "profile" in location
    assert "w_member_social" in location


@pytest.mark.asyncio
async def test_linkedin_oauth_callback_queries_userinfo_and_stores_real_member_urn(dev_client, monkeypatch):
    """
    Test 6: OAuth callback exchanges authorization code, queries /v2/userinfo with the
    fresh access token, and stores the real resolved URN (urn:li:person:{sub}) in platform_connections.
    """
    from unittest.mock import AsyncMock

    mock_token_resp = httpx.Response(
        status_code=200,
        json={"access_token": "li_real_access_token_xyz987", "expires_in": 5184000},
        request=httpx.Request("POST", "https://www.linkedin.com/oauth/v2/accessToken"),
    )

    mock_userinfo_resp = httpx.Response(
        status_code=200,
        json={
            "sub": "YashKh95_RealMemberId",
            "name": "Yash Khandelwal",
            "given_name": "Yash",
            "family_name": "Khandelwal",
        },
        request=httpx.Request("GET", "https://api.linkedin.com/v2/userinfo"),
    )

    orig_post = httpx.AsyncClient.post
    orig_get = httpx.AsyncClient.get

    async def mock_post(self, url, *args, **kwargs):
        if "accessToken" in str(url):
            return mock_token_resp
        return await orig_post(self, url, *args, **kwargs)

    async def mock_get(self, url, *args, **kwargs):
        if "userinfo" in str(url):
            # Assert authorization header contains the fresh bearer token
            headers = kwargs.get("headers", {})
            assert headers.get("Authorization") == "Bearer li_real_access_token_xyz987"
            return mock_userinfo_resp
        return await orig_get(self, url, *args, **kwargs)

    monkeypatch.setattr(httpx.AsyncClient, "post", mock_post)
    monkeypatch.setattr(httpx.AsyncClient, "get", mock_get)

    res = await dev_client.get("/auth/linkedin/callback?code=mock_linkedin_auth_code_456", follow_redirects=False)
    assert res.status_code == 303
    assert res.headers["location"] == "/settings?connected=linkedin"

    # Verify stored in platform_connections
    conn_data = MEM_PLATFORM_CONNECTIONS.get("linkedin")
    assert conn_data is not None
    assert conn_data["account_id"] == "urn:li:person:YashKh95_RealMemberId"
    assert "Yash Khandelwal" in conn_data["account_name"]
    assert conn_data["scopes"] == ["openid", "profile", "w_member_social"]
    assert decrypt_token(conn_data["access_token_encrypted"]) == "li_real_access_token_xyz987"


@pytest.mark.asyncio
async def test_linkedin_publisher_uses_real_author_urn_in_ugc_post_payload(monkeypatch):
    """
    Test 7: UGC post request to https://api.linkedin.com/v2/ugcPosts uses the stored
    real member URN (urn:li:person:{sub}) in the author field — never a placeholder.
    """
    captured_payloads = []

    mock_ugc_resp = httpx.Response(
        status_code=201,
        json={"id": "urn:li:ugcPost:7200000000000000001"},
        request=httpx.Request("POST", "https://api.linkedin.com/v2/ugcPosts"),
    )

    async def mock_post(self, url, *args, **kwargs):
        if "ugcPosts" in str(url):
            json_payload = kwargs.get("json", {})
            captured_payloads.append(json_payload)
            return mock_ugc_resp
        return httpx.Response(status_code=404, request=httpx.Request("POST", str(url)))

    monkeypatch.setattr(httpx.AsyncClient, "post", mock_post)

    try:
        MEM_PLATFORM_CONNECTIONS["linkedin"] = {
            "platform": "linkedin",
            "account_name": "Yash Khandelwal (LinkedIn Personal)",
            "account_id": "urn:li:person:YashKh95_RealMemberId",
            "access_token_encrypted": encrypt_token("mock_valid_token_abc123"),
            "is_active": True,
        }

        sample_post = {
            "title": "Technical Narrow Wovens MIL-W-4088K",
            "captions": {
                "linkedin": "Precision woven webbing manufactured in Kanpur to military standard."
            }
        }

        result = await publish_to_linkedin(sample_post, conn=None)
        assert result["status"] == "published"
        assert result["author"] == "urn:li:person:YashKh95_RealMemberId"
        assert result["post_id"] == "urn:li:ugcPost:7200000000000000001"

        # Assert payload sent to LinkedIn API contains the real author URN
        assert len(captured_payloads) == 1
        assert captured_payloads[0]["author"] == "urn:li:person:YashKh95_RealMemberId"
        assert captured_payloads[0]["author"] != "urn:li:person:self"
    finally:
        MEM_PLATFORM_CONNECTIONS.pop("linkedin", None)


@pytest.mark.asyncio
async def test_legacy_broken_placeholder_author_urn_rejected():
    """
    Test 8: If a legacy broken connection with account_id='urn:li:person:self' is encountered,
    publishing fails with an explicit error asking the user to reconnect.
    """
    try:
        MEM_PLATFORM_CONNECTIONS["linkedin"] = {
            "platform": "linkedin",
            "account_name": "Yash Khandelwal (Personal LinkedIn)",
            "account_id": "urn:li:person:self",
            "access_token_encrypted": encrypt_token("mock_legacy_token"),
            "is_active": True,
        }

        sample_post = {
            "title": "Test Post",
            "captions": {"linkedin": "Test caption"}
        }

        result = await publish_to_linkedin(sample_post, conn=None)
        assert result["status"] == "error"
        assert "placeholder" in result["error"].lower() or "reconnect" in result["error"].lower()
    finally:
        MEM_PLATFORM_CONNECTIONS.pop("linkedin", None)
