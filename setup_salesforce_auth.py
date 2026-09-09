#!/usr/bin/env python3
"""
Run this once per Salesforce org: python3 setup_salesforce_auth.py

Opens a browser, you log in and approve access, and a refresh token is
saved to .salesforce_token.json (gitignored). server.py uses that token
silently after this — you won't need to log in again unless the org
revokes access.
"""
from dotenv import load_dotenv

load_dotenv()

import salesforce_auth

if __name__ == "__main__":
    salesforce_auth.run_login_flow()
