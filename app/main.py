"""Vercel entrypoint for the read-only web viewer.

Only this module and its imports (culture.web.*, culture.models.*,
culture.database, culture.utils.dates) get exercised — none of them import
the pipeline's heavy deps (feedparser, trafilatura, yt-dlp, Anthropic SDK),
so those never need to be in the deployed bundle. See requirements.txt.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from culture.web.app import create_app  # noqa: E402

app = create_app(require_auth=True)
