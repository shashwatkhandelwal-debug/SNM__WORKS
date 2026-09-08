#!/usr/bin/env python3
import datetime
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))


from jose import jwt
from config import settings

def main():
    now = datetime.datetime.now(datetime.timezone.utc)
    payload = {
        "sub": "00000000-0000-0000-0000-000000000001",
        "email": "yashkhandelwal95@gmail.com",
        "role": "authenticated",
        "user_metadata": {"full_name": "Yash Khandelwal", "role": "owner"},
        "exp": int((now + datetime.timedelta(hours=24)).timestamp()),
        "iat": int(now.timestamp()),
    }
    secret = settings.secret_key
    token = jwt.encode(payload, secret, algorithm="HS256")

    print(token)
    print()
    print(f"document.cookie = \"access_token={token}; path=/\";")

if __name__ == "__main__":
    main()
