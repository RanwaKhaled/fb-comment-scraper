import streamlit as st
import pandas as pd
import subprocess
import sys
import os
import httpx
import asyncio

# ── Page config ───────────────────────────────────────────────────────────────
st.set_page_config(
    page_title="Facebook Comment Scraper",
    page_icon="💬",
    layout="centered",
)

st.title("💬 Facebook Comment Scraper")
st.markdown("Scrape comments from any public Facebook post and view them as a table.")

# ── Inputs ────────────────────────────────────────────────────────────────────
post_url = st.text_input(
    "Facebook Post URL",
    placeholder="https://www.facebook.com/page/posts/...",
)

max_comments = st.slider("Max comments to scrape", min_value=5, max_value=200, value=10, step=5)

EMAIL = st.secrets["FB_EMAIL"]
PASSWORD = st.secrets["FB_PASSWORD"]

# ── Session state init ────────────────────────────────────────────────────────
for key, default in {
    "df_original": None,
    "df_filtered": None,
    "showing_filtered": False,
    "is_filtering": False,
}.items():
    if key not in st.session_state:
        st.session_state[key] = default

# ── Filter logic ──────────────────────────────────────────────────────────────
FILTER_COMMENTS_PROMPT = """
You are an AI assistant tasked with filtering comments under a social media advertisement post. Your job is to determine if a comment is relevant to the business/product or irrelevant.

Context: The post is an advertisement for a product, service, or business. 

Definitions:
- Relevant (1): Comments expressing interest, asking for details, price inquiries ("how much", "hm", "details", "السعر كام"), asking about the location, delivery, or specific questions about the product/service.
- Irrelevant (0): Comments just tagging/mentioning a friend, telling jokes, insults, generic religious prayers/dua (e.g., "ربنا يوفقك"), engagement bait/reach farming, or unrelated chatter.

Output Format:
Return exactly and only "1" if the comment is relevant, or "0" if it is irrelevant. Do not include any explanations, punctuation, intro, or outro text.

Comment to evaluate:
<text>
"""

async def generate_output(prompt):
    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.post(
            st.secrets['AZURE_OPENAI_ENDPOINT'],
            headers={
                "api-key": st.secrets['AZURE_OPENAI_API_KEY'],
                "Content-Type": "application/json"
            },
            json={
                "messages": [{"role": "user", "content": prompt}],
                "max_completion_tokens": 10,
            }
        )
        resp.raise_for_status()
        return resp.json()["choices"][0]["message"]["content"].strip()

def run_filter(df: pd.DataFrame) -> pd.DataFrame:
    keep_indices = []
    progress = st.progress(0, text="Filtering comments...")
    total = len(df)

    for i, (idx, row) in enumerate(df.iterrows()):
        prompt = FILTER_COMMENTS_PROMPT.replace("<text>", str(row["comment_text"]))
        try:
            output = asyncio.run(generate_output(prompt))
        except Exception as e:
            st.warning(f"Row {idx} skipped due to error: {e}")
            output = "1"  # keep on error

        if output == "1":
            keep_indices.append(idx)

        progress.progress((i + 1) / total, text=f"Filtering... {i+1}/{total}")

    progress.empty()
    # Build filtered df from kept indices — handles empty result correctly
    return df.loc[keep_indices].reset_index(drop=True)

# ── Scraper runner ────────────────────────────────────────────────────────────
def run_scraper(post_url, email, password, max_comments, output_csv):
    try:
        result = subprocess.run(
            [sys.executable, "fb_scraper.py",
             post_url, email, password, str(max_comments), output_csv],
            capture_output=True, text=True, timeout=400
        )
        return result.returncode, result.stdout, result.stderr
    except Exception as e:
        return 1, "", str(e)

# ── Table renderer ────────────────────────────────────────────────────────────
def render_table(df: pd.DataFrame):
    if "profile_url" in df.columns:
        df_display = df.copy()
        df_display["profile_url"] = df_display["profile_url"].apply(
            lambda u: f'<a href="{u}" target="_blank">View Profile</a>' if u else ""
        )
        st.write(df_display.to_html(escape=False, index=False), unsafe_allow_html=True)
    else:
        st.dataframe(df, use_container_width=True)

# ── Scrape button ─────────────────────────────────────────────────────────────
if st.button("🚀 Scrape Comments", type="primary", use_container_width=True):
    if not post_url.strip():
        st.error("Please enter a Facebook post URL.")
    else:
        output_csv = "fb_comments.csv"
        st.session_state.df_original = None
        st.session_state.df_filtered = None
        st.session_state.showing_filtered = False
        st.session_state.is_filtering = False

        with st.spinner("🔄 Launching browser, logging in, and scraping comments... (this can take 1–3 minutes)"):
            status_box = st.empty()
            status_box.info("⏳ Scraping in progress — please wait.")
            returncode, stdout, stderr = run_scraper(post_url, EMAIL, PASSWORD, max_comments, output_csv)
            status_box.empty()

        if returncode != 0:
            st.error("❌ Scraper encountered an error.")
            with st.expander("Show error details"):
                st.code(stderr or stdout or "No output captured.")
        elif not os.path.exists(output_csv) or os.path.getsize(output_csv) == 0:
            st.warning("⚠️ No comments were found or saved.")
            if stdout or stderr:
                with st.expander("Show output"):
                    st.code(stdout + "\n" + stderr)
        else:
            st.session_state.df_original = pd.read_csv(output_csv, encoding="utf-8-sig")

# ── Results section ───────────────────────────────────────────────────────────
if st.session_state.df_original is not None:
    df_orig = st.session_state.df_original
    is_filtered = st.session_state.showing_filtered

    # df_filtered is None = not run yet; DataFrame (even empty) = ran
    if is_filtered and st.session_state.df_filtered is not None:
        df_display = st.session_state.df_filtered
    else:
        df_display = df_orig

    # ── Header ────────────────────────────────────────────────────────────────
    if is_filtered:
        kept = len(st.session_state.df_filtered)
        total = len(df_orig)
        if kept == 0:
            st.warning(f"🔍 No relevant comments found out of {total} scraped.")
        else:
            st.success(f"🔍 Showing **{kept}** relevant comment(s) out of {total} scraped.")
    else:
        st.success(f"✅ Scraped **{len(df_orig)}** comment(s) successfully!")

    # ── Table ─────────────────────────────────────────────────────────────────
    st.subheader("📋 Comments")
    if df_display.empty:
        st.info("No comments to display.")
    else:
        render_table(df_display)

    # ── Action buttons (hidden while filtering is running) ────────────────────
    if not st.session_state.is_filtering:
        col1, col2 = st.columns(2)

        with col1:
            if not is_filtered:
                if st.button("📑 Filter Relevant Comments", use_container_width=True):
                    if st.session_state.df_filtered is not None:
                        # it's already filtered so just toggle
                        st.session_state.showing_filtered = True
                        st.rerun()
                    else:
                        # need to filter for the first time
                        st.session_state.is_filtering = True
                        st.rerun()
            else:
                if st.button("👁️ See Original Comments", use_container_width=True):
                    st.session_state.showing_filtered = False
                    st.rerun()

        with col2:
            csv_bytes = df_display.to_csv(index=False, encoding="utf-8-sig").encode("utf-8-sig")
            st.download_button(
                label="⬇️ Download Filtered CSV" if is_filtered else "⬇️ Download CSV",
                data=csv_bytes,
                file_name="filtered_comments.csv" if is_filtered else "facebook_comments.csv",
                mime="text/csv",
                use_container_width=True,
            )

    # ── Filtering runs on its own rerun, isolated from button render ──────────
    if st.session_state.is_filtering and st.session_state.df_filtered is None:
        with st.spinner("🧠 AI is filtering comments..."):
            st.session_state.df_filtered = run_filter(df_orig.copy())
        st.session_state.is_filtering = False
        st.session_state.showing_filtered = True
        st.rerun()

st.markdown("---")
st.caption("Your credentials are used only during the scrape session and are never stored.")