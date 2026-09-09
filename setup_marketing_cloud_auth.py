#!/usr/bin/env python3
"""
Run this once: python3 setup_marketing_cloud_auth.py

Marketing Cloud uses server-to-server client credentials (no browser
login) so this just verifies the MC_* values in .env work and caches the
first access token.
"""
from dotenv import load_dotenv

load_dotenv()

import marketing_cloud_auth

if __name__ == "__main__":
    marketing_cloud_auth.verify_connection()
