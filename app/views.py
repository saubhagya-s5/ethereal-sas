"""One function per page of the Streamlit app. Data comes from output/, falling back to contracts/."""
import html
import json
import re

import pandas as pd
import streamlit as st

from generator import brief as brief_mod
from generator import llm, page, simulate
from generator.data import CITY_SLUGS, PAGES, ROOT, city_of_query, inventory, label, load

ENGINES = ["chatgpt", "gemini", "perplexity", "claude"]
DEMO_PLACE_TYPES = {"locality", "landmark"}


# ---------- helpers ----------

def city() -> str:
    return st.session_state.get("city", "Bengaluru")


def sample_note(*names: str) -> None:
    missing = [n for n in names if load(n)[1]]
    if missing:
        st.caption(f"Showing contract sample data: no output/{', output/'.join(m + '.json' for m in missing)} yet.")


def city_results() -> tuple[list[dict], list[dict]]:
    queries, _ = load("queries")
    results, _ = load("ai_results")
    return ([q for q in queries if q["city"] == city()],
            [r for r in results if city_of_query(r["query_id"], queries) == city()])


def city_opps(demo_only: bool = True) -> list[dict]:
    """By default only locality and landmark opportunities: the demo pages are never city-level."""
    opps, _ = load("opportunities")
    return sorted((o for o in opps if o["city"] == city() and (not demo_only or o["place_type"] in DEMO_PLACE_TYPES)),
                  key=lambda o: -o["score"])


def default_opp(opps: list[dict]) -> str:
    """Budget pages first (e.g. Koramangala or Indiranagar + budget), then the highest score."""
    budget = [o for o in opps if "budget" in o["filter"]]
    return (budget or opps)[0]["opp_id"]


def pick_opp() -> dict | None:
    opps = city_opps()
    if not opps:
        st.info(f"No locality or landmark opportunities for {city()} yet.")
        return None
    ids = [o["opp_id"] for o in opps]
    current = st.session_state.get("opp_id")
    current = current if current in ids else default_opp(opps)
    choice = st.selectbox("Opportunity", ids, index=ids.index(current),
                          format_func=lambda i: f"{i}  ·  score {next(o['score'] for o in opps if o['opp_id'] == i):.2f}")
    st.session_state["opp_id"] = choice
    return next(o for o in opps if o["opp_id"] == choice)


def use_llm_toggle(key: str) -> bool:
    if llm.available():
        return st.toggle(f"Write copy with Claude ({llm.MODEL})", value=True, key=key)
    st.caption("No ANTHROPIC_API_KEY in .env: copy is built from templates (hotel facts are the same either way).")
    return False


def raw_answer(result: dict) -> str | None:
    path = ROOT / result["raw_answer_path"]
    if not path.exists():
        return None
    text = path.read_text(encoding="utf-8")
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        return text
    if isinstance(data, dict):
        for key in ("answer", "text", "content", "raw_answer", "output"):
            if isinstance(data.get(key), str):
                return data[key]
    return json.dumps(data, indent=2)


def highlight(text: str, brands: list[str]) -> str:
    out = html.escape(text)
    for brand in sorted(set(brands) | {"Treebo"}, key=len, reverse=True):
        colour = "#bbf7d0" if brand.lower() == "treebo" else "#fde68a"
        out = re.sub(rf"\b({re.escape(html.escape(brand))})", rf'<mark style="background:{colour};padding:0 2px">\1</mark>',
                     out, flags=re.I)
    return f'<div style="white-space:pre-wrap;line-height:1.6">{out}</div>'


def flags_table(flags: list[dict]) -> None:
    if not flags:
        st.success("Guardrail: every hotel fact matches inventory.")
        return
    blocked = any(f["severity"] == "block" for f in flags)
    (st.error if blocked else st.warning)(f"Guardrail: {len(flags)} issue(s){' - blocked' if blocked else ''}.")
    st.dataframe(pd.DataFrame(flags)[["severity", "field", "claim", "truth", "text"]], hide_index=True, width="stretch")


# ---------- pages ----------

def dashboard() -> None:
    st.title(f"AI visibility · {city()}")
    sample_note("city_scores", "ai_results")
    scores, _ = load("city_scores")

    board = pd.DataFrame([{
        "City": s["city"], "Mention rate": s["mention_rate"], "Share of voice": s["share_of_voice"],
        "Wrong facts": s["wrong_facts"], "Top competitor": s["top_competitor"],
    } for s in scores]).sort_values("Mention rate", ascending=False)
    st.subheader("City leaderboard")
    st.dataframe(board, hide_index=True, width="stretch", column_config={
        "Mention rate": st.column_config.ProgressColumn(format="percent", min_value=0, max_value=1),
        "Share of voice": st.column_config.ProgressColumn(format="percent", min_value=0, max_value=1),
    })

    mine = next((s for s in scores if s["city"] == city()), None)
    if not mine:
        st.info(f"No scores for {city()} yet.")
    else:
        cols = st.columns(4)
        cols[0].metric("Treebo mention rate", f"{mine['mention_rate']:.0%}")
        cols[1].metric("Share of voice", f"{mine['share_of_voice']:.0%}")
        cols[2].metric("Wrong facts", mine["wrong_facts"])
        cols[3].metric("Top competitor", mine["top_competitor"])
        st.subheader("Mention rate by engine")
        engines = pd.DataFrame({"Engine": list(mine["by_engine"]), "Mention rate": list(mine["by_engine"].values())})
        st.bar_chart(engines, x="Engine", y="Mention rate", horizontal=True, height=220)

    st.subheader("Wrong or outdated facts AI states about Treebo")
    queries, results = city_results()
    text = {q["query_id"]: q["text"] for q in queries}
    inv = inventory()
    wrong = [{
        "Engine": r["engine"], "Run": r["run"], "Question": text.get(r["query_id"], r["query_id"]),
        "Hotel": inv.get(str(c["hotel_id"]), {}).get("name", c["hotel_id"]),
        "AI said": c["claim"], "Status": c["status"], "Truth": c["truth"],
    } for r in results for c in r["treebo_claims"] if c["status"] != "correct"]
    if wrong:
        st.dataframe(pd.DataFrame(wrong), hide_index=True, width="stretch")
    else:
        st.success("No wrong facts found.")


def coverage() -> None:
    st.title(f"Coverage & opportunities · {city()}")
    sample_note("opportunities")
    opps = city_opps(demo_only=False)
    inv = inventory()
    in_opp = {str(i) for o in opps for i in o["matching_hotel_ids"]}
    hotels = [h for h in inv.values() if h["city"] == city() and h["active"]]

    st.subheader("Treebo hotels")
    st.caption("🟠 matches a proposed page · 🟢 other active hotels")
    points = pd.DataFrame([{
        "lat": h["lat"], "lng": h["lng"], "name": h["name"],
        "color": "#f97316" if h["hotel_id"] in in_opp else "#16a34a",
    } for h in hotels])
    if not points.empty:
        st.map(points, latitude="lat", longitude="lng", color="color", size=120, height=380)
    localities = pd.Series([h["locality"] for h in hotels]).value_counts().rename_axis("Locality").reset_index(name="Active hotels")
    with st.expander("Active hotels by locality"):
        st.dataframe(localities, hide_index=True, width="stretch")

    st.subheader("Ranked page opportunities")
    if not opps:
        st.info(f"No opportunities for {city()} yet.")
        return
    table = pd.DataFrame([{
        "Score": o["score"], "Place": o["place"], "Filter": label(o["filter"]),
        "Proposed URL": o["proposed_url"], "On Treebo": o["exists_on_treebo"],
        "Competitor pages": len(o["competitors_with_page"]), "Search volume": o["search_volume"],
        "Treebo hotels": len(o["matching_hotel_ids"]), "AI mention rate": o["ai_mention_rate"], "opp_id": o["opp_id"],
    } for o in opps])
    st.dataframe(table, hide_index=True, width="stretch", column_config={
        "Score": st.column_config.ProgressColumn(format="%.2f", min_value=0, max_value=1),
        "Proposed URL": st.column_config.LinkColumn(),
        "AI mention rate": st.column_config.NumberColumn(format="percent"),
    })
    for o in opps:
        if o["competitors_with_page"]:
            with st.expander(f"Competitor pages for {o['opp_id']}"):
                for url in o["competitors_with_page"]:
                    st.markdown(f"- {url}")


def ai_answers() -> None:
    st.title(f"AI answers · {city()}")
    sample_note("queries", "ai_results")
    queries, results = city_results()
    if not results:
        st.info(f"No AI results for {city()} yet.")
        return
    by_id = {q["query_id"]: q for q in queries}
    qids = list(dict.fromkeys(r["query_id"] for r in results))
    qid = st.selectbox("Question", qids, format_func=lambda i: by_id[i]["text"] if i in by_id else i)
    if qid in by_id:
        st.caption(" · ".join(f"{k}: {v}" for k, v in by_id[qid]["tags"].items() if v))
    inv = inventory()

    for r in sorted((r for r in results if r["query_id"] == qid), key=lambda r: (ENGINES.index(r["engine"]) if r["engine"] in ENGINES else 9, r["run"])):
        status = f"✅ Treebo #{r['treebo_rank']}" if r["treebo_mentioned"] else "❌ Treebo not mentioned"
        with st.expander(f"{r['engine'].title()} · run {r['run']} · {status}", expanded=True):
            answer = raw_answer(r)
            if answer:
                st.markdown(highlight(answer, r["brands_mentioned"]), unsafe_allow_html=True)
            else:
                st.caption(f"Raw answer not cached locally ({r['raw_answer_path']}).")
            st.markdown("**Brands mentioned:** " + " ".join(
                f"`{b}`" if b.lower() != "treebo" else f"**`{b}`**" for b in r["brands_mentioned"]))
            if r["cited_urls"]:
                st.markdown("**Cited sources**")
                for url in r["cited_urls"]:
                    st.markdown(f"- {'🟢 ' if 'treebo.com' in url else ''}{url}")
            if r["treebo_claims"]:
                st.markdown("**Fact check**")
                st.dataframe(pd.DataFrame([{
                    "": {"correct": "✅", "wrong": "❌", "outdated": "⚠️"}.get(c["status"], "?"),
                    "Hotel": inv.get(str(c["hotel_id"]), {}).get("name", c["hotel_id"]),
                    "Claim": c["claim"], "Status": c["status"], "Truth": c["truth"],
                } for c in r["treebo_claims"]]), hide_index=True, width="stretch")
            if r["miss_reason"]:
                st.warning(f"Miss reason: **{r['miss_reason']}**")


def brief() -> None:
    st.title("Content brief")
    opp = pick_opp()
    if not opp:
        return
    use_llm = use_llm_toggle("brief_llm")
    key = f"brief_report_{opp['opp_id']}"
    if st.button("Generate brief", type="primary"):
        with st.spinner("Writing brief..."):
            b, report = brief_mod.build_brief(opp, use_llm=use_llm)
            if not report["guardrail"]["blocked"]:
                brief_mod.save_brief(b)
            st.session_state[key] = (b, report)
    saved = st.session_state.get(key)
    b = saved[0] if saved else brief_mod.load_brief(opp["opp_id"])
    if not b:
        st.info("No brief yet for this opportunity. Generate one above.")
        return
    if saved:
        st.caption(f"Copy source: {saved[1]['source']}")
        flags_table(saved[1]["guardrail"]["flags"])

    st.subheader(b["h1"])
    st.caption(f"Title: {b['title']}  \nMeta: {b['meta_description']}  \nURL: {opp['proposed_url']}")
    st.info(b["quick_answer"])
    inv = inventory()
    left, right = st.columns([3, 2])
    with left:
        st.markdown("**FAQs**")
        for f in b["faqs"]:
            with st.expander(f["q"]):
                st.write(f["a"])
    with right:
        st.markdown("**Sections**")
        st.markdown("\n".join(f"{i}. {s}" for i, s in enumerate(b["sections"], 1)))
        st.markdown(f"**Hotels** (sorted by {b['sort_by']})")
        st.markdown("\n".join(f"- {inv[i]['name']} · {inv[i]['locality']} · {inv[i]['rating'] or 'no rating'}" for i in b["hotel_ids"] if i in inv))
        st.markdown("**Internal links**")
        st.markdown("\n".join(f"- {u}" for u in b["internal_links"]))
    with st.expander("Brief JSON"):
        st.json(b)


def page_preview() -> None:
    st.title("Landing page preview")
    opp = pick_opp()
    if not opp:
        return
    c1, c2 = st.columns(2)
    with c1:
        use_llm = use_llm_toggle("page_llm")
    with c2:
        deep = st.toggle("Deep fact-check (Claude extracts claims too)", value=False, disabled=not llm.available())
    key = f"page_{opp['opp_id']}"
    if st.button("Generate page", type="primary"):
        with st.spinner("Building page and checking facts..."):
            st.session_state[key] = page.generate(opp, use_llm=use_llm, deep_check=deep)

    result = st.session_state.get(key)
    path = PAGES / f"{opp['opp_id']}.html"
    if result:
        page_html, report = result["html"], result["report"]
    elif path.exists():
        page_html = path.read_text(encoding="utf-8")
        report_path = PAGES / f"{opp['opp_id']}.guardrail.json"
        report = json.loads(report_path.read_text(encoding="utf-8")) if report_path.exists() else None
    else:
        st.info("No page yet for this opportunity. Generate one above.")
        return

    if report:
        st.caption(f"Brief copy: {report['brief_source']} · area copy: {report['about_source']}")
        flags_table(report["guardrail"]["flags"])
        if report["guardrail"].get("blocked_before_fallback"):
            with st.expander("Claude copy the guardrail blocked (replaced with template copy)"):
                st.dataframe(pd.DataFrame(report["guardrail"]["blocked_before_fallback"]), hide_index=True)
    if result and result["report"]["guardrail"]["blocked"]:
        st.error("Not saved: the page still has blocked claims.")

    view = st.radio("View", ["Desktop", "Mobile"], horizontal=True, label_visibility="collapsed")
    st.download_button("Export HTML", page_html, file_name=f"{opp['opp_id']}.html", mime="text/html")
    # Safe to embed: the template autoescapes all copy, including LLM text.
    st.iframe(page_html, width=390 if view == "Mobile" else "stretch", height=1600)


def tracking() -> None:
    st.title(f"Tracking · {city()}")
    opp = pick_opp()
    if opp:
        st.subheader("Before / after simulation")
        st.caption("Claude answers each question from the pages AI engines cited, with and without our new page.")
        has_page = (PAGES / f"{opp['opp_id']}.html").exists()
        runs = st.slider("Runs per question", 1, 5, 3)
        if not llm.available():
            st.caption("Needs ANTHROPIC_API_KEY in .env.")
        elif not has_page:
            st.caption("Generate the page first (Page preview).")
        if st.button("Run simulation", type="primary", disabled=not (llm.available() and has_page)):
            with st.spinner("Asking Claude before and after..."):
                simulate.run(opp, runs=runs)
        sim = simulate.load_result(opp["opp_id"])
        if sim:
            before, after = sim["before_mention_rate"], sim["after_mention_rate"]
            cols = st.columns(3)
            cols[0].metric("Before: Treebo mentioned", f"{before:.0%}" if before is not None else "–")
            cols[1].metric("After: Treebo mentioned", f"{after:.0%}" if after is not None else "–",
                           delta=f"{(after - before) * 100:+.0f} pts" if None not in (before, after) else None)
            cols[2].metric("Model · runs", f"{sim['model']} · {sim['runs']}")
            st.caption(f"Run at {sim['created_at']}")
            for q in sim["queries"]:
                b = sum(r["treebo_mentioned"] for r in q["before"])
                a = sum(r["treebo_mentioned"] for r in q["after"])
                with st.expander(f"{q['question']} · before {b}/{len(q['before'])} · after {a}/{len(q['after'])}"):
                    st.caption("Sources: " + (", ".join(q["sources"]) or "none could be fetched"))
                    for arm in ("before", "after"):
                        for r in q[arm][:1]:
                            st.markdown(f"**{arm.title()} (run {r['run']})**")
                            st.markdown(highlight(r["answer"], []), unsafe_allow_html=True)
        else:
            st.info("No simulation yet for this opportunity.")

    st.subheader("Existing page issues")
    issues, sample = load("page_issues")
    if sample:
        st.caption("Showing contract sample data: no output/page_issues.json yet.")
    slug = CITY_SLUGS.get(city(), "")
    issues = [i for i in issues if slug in i["url"]] or issues
    st.dataframe(pd.DataFrame(issues), hide_index=True, width="stretch",
                 column_config={"url": st.column_config.LinkColumn("URL")})
