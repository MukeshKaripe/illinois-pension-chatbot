"""Test-wide settings that must not come from a developer's `.env`.

`config.Settings` reads `.env`, which on a working machine points at a real
deployment. Settings that change agent behaviour are pinned to their defaults
here; a test about one of them sets it itself.
"""
from __future__ import annotations

from pension_chat.config import get_settings

# DEPLOYMENT_ARTICLE=8 in a client `.env` turns every record-less turn into an
# Article 8 one; the loop's tests are about a deployment that serves every fund.
get_settings().deployment_article = None
