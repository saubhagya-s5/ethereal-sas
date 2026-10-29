"""Treebo AEO Agent - Streamlit UI.

Run from the repo root: streamlit run app/main.py
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import streamlit as st  # noqa: E402

from app import views  # noqa: E402
from generator.data import CITY_SLUGS  # noqa: E402

st.set_page_config(page_title="Treebo AEO Agent", page_icon="🏨", layout="wide")

with st.sidebar:
    st.markdown("### Treebo AEO Agent")
    st.selectbox("City", list(CITY_SLUGS), index=1, key="city")

pages = [
    st.Page(views.dashboard, title="Dashboard", icon=":material/leaderboard:", default=True),
    st.Page(views.coverage, title="Coverage & opportunities", icon=":material/map:", url_path="coverage"),
    st.Page(views.ai_answers, title="AI answers", icon=":material/forum:", url_path="answers"),
    st.Page(views.brief, title="Brief", icon=":material/description:", url_path="brief"),
    st.Page(views.page_preview, title="Page preview", icon=":material/web:", url_path="preview"),
    st.Page(views.tracking, title="Tracking", icon=":material/monitoring:", url_path="tracking"),
]
st.navigation(pages).run()
