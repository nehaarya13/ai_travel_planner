import os
import re
import streamlit as st
from datetime import datetime
from langchain_core.messages import HumanMessage
from main import app

# ── PDF export (UI feature) ───────────────────────────────────────────────────
try:
    from fpdf import FPDF
except ImportError:          # app still works; the PDF button just shows a hint
    FPDF = None

_FONT_PAIRS = [("DejaVuSans.ttf", "DejaVuSans-Bold.ttf"), ("arial.ttf", "arialbd.ttf")]
_FONT_DIRS = ["/usr/share/fonts/truetype/dejavu", "/usr/share/fonts/dejavu", "/usr/share/fonts/TTF",
              "/usr/share/fonts/truetype", "/Library/Fonts", "/System/Library/Fonts/Supplemental",
              "C:/Windows/Fonts", os.path.join(os.path.dirname(__file__), "fonts")]
try:  # matplotlib ships DejaVu, handy on hosts without system fonts
    import matplotlib
    _FONT_DIRS.append(os.path.join(os.path.dirname(matplotlib.__file__), "mpl-data", "fonts", "ttf"))
except Exception:
    pass


def _find_unicode_font():
    for reg, bold in _FONT_PAIRS:
        for d in _FONT_DIRS:
            r, b = os.path.join(d, reg), os.path.join(d, bold)
            if os.path.exists(r) and os.path.exists(b):
                return r, b
    return None


def _clean(text, unicode_ok):
    """Make LLM markdown safe for the PDF font: drop emoji, tidy inline markdown."""
    text = re.sub(r"\[([^\]]+)\]\((https?://[^)]+)\)", r"\1 (\2)", text)   # links
    text = re.sub(r"`([^`]*)`", r"\1", text)                                # inline code
    text = re.sub(r"(?<!\*)\*(?!\*)([^*\n]+)(?<!\*)\*(?!\*)", r"\1", text)  # *italic*
    text = text.replace("--", "–" if unicode_ok else "-")
    out = []
    for ch in text:
        o = ord(ch)
        if o >= 0x1F000 or o in (0xFE0F, 0x200D) or 0x2600 <= o <= 0x27BF or 0x2B00 <= o <= 0x2BFF:
            continue
        out.append(ch)
    text = "".join(out)
    if not unicode_ok:
        for a, b in (("₹", "Rs."), ("•", "-"), ("–", "-"), ("—", "-"), ("‘", "'"), ("’", "'"),
                     ("“", '"'), ("”", '"'), ("…", "..."), ("→", "->")):
            text = text.replace(a, b)
        text = text.encode("latin-1", "ignore").decode("latin-1")
    return text


def build_pdf(query, user_id, generated, sections, llm_calls):
    """Return PDF bytes, or None if fpdf2 isn't installed."""
    if FPDF is None:
        return None

    fonts = _find_unicode_font()
    unicode_ok = fonts is not None
    TEAL, INK, MUTED, LINE = (15, 118, 110), (30, 41, 59), (100, 116, 139), (215, 221, 228)

    class PlanPDF(FPDF):
        def footer(self):
            self.set_y(-14)
            self.set_font(FAMILY, "", 8)
            self.set_text_color(*MUTED)
            self.cell(0, 8, f"AI Travel Booking System  ·  page {self.page_no()}" if unicode_ok
                      else f"AI Travel Booking System  -  page {self.page_no()}", align="C")

    pdf = PlanPDF(format="A4")
    if unicode_ok:
        pdf.add_font("Body", "", fonts[0])
        pdf.add_font("Body", "B", fonts[1])
        FAMILY = "Body"
    else:
        FAMILY = "Helvetica"
    pdf.set_margins(18, 18, 18)
    pdf.set_auto_page_break(True, margin=18)
    pdf.add_page()

    def txt(s):
        return _clean(str(s), unicode_ok)

    def para(s, size=10.5, indent=0, bullet=None, h=5.6):
        pdf.set_font(FAMILY, "", size)
        pdf.set_text_color(*INK)
        pdf.set_x(pdf.l_margin + indent)
        if bullet:
            pdf.cell(6, h, bullet)
        pdf.multi_cell(0, h, txt(s), markdown=True, align="L", new_x="LMARGIN", new_y="NEXT")

    def heading(s, size, color=INK, before=4, after=1.5):
        pdf.ln(before)
        pdf.set_font(FAMILY, "B", size)
        pdf.set_text_color(*color)
        pdf.multi_cell(0, size * 0.55, txt(re.sub(r"\*\*", "", s)), new_x="LMARGIN", new_y="NEXT")
        pdf.ln(after)

    def table(rows):
        rows = [[re.sub(r"\*\*", "", txt(c)) for c in r] for r in rows]
        width = max(len(r) for r in rows)
        rows = [r + [""] * (width - len(r)) for r in rows]
        try:
            pdf.set_font(FAMILY, "", 9)
            pdf.set_text_color(*INK)
            pdf.set_draw_color(*LINE)
            from fpdf.fonts import FontFace
            pdf.set_fill_color(255, 255, 255)
            head = FontFace(emphasis="BOLD", color=INK, fill_color=(226, 240, 238))
            with pdf.table(text_align="LEFT", line_height=5, first_row_as_headings=True,
                           headings_style=head, cell_fill_color=(255, 255, 255),
                           cell_fill_mode="ROWS", borders_layout="HORIZONTAL_LINES") as t:
                for r in rows:
                    row = t.row()
                    for c in r:
                        row.cell(c)
            pdf.ln(2)
        except Exception:
            for r in rows:
                para("  |  ".join(r), size=9.5)

    def markdown_block(md):
        lines = md.splitlines()
        i = 0
        while i < len(lines):
            line = lines[i].rstrip()
            s = line.strip()
            if not s:
                pdf.ln(2)
            elif re.fullmatch(r"[-*_]{3,}", s):
                pdf.ln(1.5)
                pdf.set_draw_color(*LINE)
                pdf.line(pdf.l_margin, pdf.get_y(), pdf.w - pdf.r_margin, pdf.get_y())
                pdf.ln(3)
            elif s.startswith("|"):
                block = []
                while i < len(lines) and lines[i].strip().startswith("|"):
                    cells = [c.strip() for c in lines[i].strip().strip("|").split("|")]
                    if not all(re.fullmatch(r":?-{2,}:?", c) for c in cells if c):
                        block.append(cells)
                    i += 1
                if block:
                    table(block)
                continue
            elif re.match(r"#{1,6}\s", s):
                level = len(s) - len(s.lstrip("#"))
                heading(s[level:].strip(), {1: 15, 2: 13, 3: 11.5}.get(level, 10.5), before=3)
            elif re.match(r"[-*+]\s+", line.lstrip()):
                indent = (len(line) - len(line.lstrip())) // 2 * 5
                para(re.sub(r"^[-*+]\s+", "", line.lstrip()), indent=indent + 2, bullet="•" if unicode_ok else "-")
            elif re.match(r"\d+[.)]\s+", s):
                num, rest = re.match(r"(\d+[.)])\s+(.*)", s).groups()
                para(rest, indent=2, bullet=num)
            else:
                para(s)
            i += 1

    # Title block
    pdf.set_fill_color(*TEAL)
    pdf.rect(0, 0, pdf.w, 6, "F")
    pdf.set_y(18)
    pdf.set_font(FAMILY, "B", 24)
    pdf.set_text_color(*INK)
    pdf.cell(0, 11, "Travel Plan", new_x="LMARGIN", new_y="NEXT")
    pdf.set_font(FAMILY, "", 10)
    pdf.set_text_color(*MUTED)
    pdf.multi_cell(0, 5.5, txt(f"Query: {query}"), align="L", new_x="LMARGIN", new_y="NEXT")
    pdf.cell(0, 5.5, txt(f"Generated: {generated}    User ID: {user_id}    LLM calls: {llm_calls}"),
             new_x="LMARGIN", new_y="NEXT")

    for title, body in sections:
        pdf.ln(5)
        pdf.set_font(FAMILY, "B", 14)
        pdf.set_text_color(*TEAL)
        pdf.cell(0, 8, title, new_x="LMARGIN", new_y="NEXT")
        pdf.set_draw_color(*TEAL)
        pdf.set_line_width(0.4)
        pdf.line(pdf.l_margin, pdf.get_y(), pdf.w - pdf.r_margin, pdf.get_y())
        pdf.set_line_width(0.2)
        pdf.ln(3)
        markdown_block(body or "N/A")

    return bytes(pdf.output())


st.set_page_config(
    page_title="AI Travel Booking System",
    page_icon="✈️",
    layout="wide"
)

# ─────────────────────────────────────────────────────────────────────────────
# UI THEME
# Colours come from Streamlit's own theme (⋮ menu → Settings → Light / Dark /
# System), so this stylesheet works in both modes. Borders, cards and muted text
# are derived from the current text colour; only the teal accent is fixed.
# ─────────────────────────────────────────────────────────────────────────────
st.markdown("""
<style>
@import url('https://fonts.googleapis.com/css2?family=Bricolage+Grotesque:opsz,wght@12..96,600;12..96,700&family=Instrument+Sans:wght@400;500;600&display=swap');

.stApp {
    --accent: #14B8A6;
    --accent-deep: #0F766E;
    --line:  color-mix(in srgb, currentColor 16%, transparent);
    --soft:  color-mix(in srgb, currentColor 5%, transparent);
    --muted: color-mix(in srgb, currentColor 62%, transparent);
}
html, body, .stApp, button, input, textarea {
    font-family: 'Instrument Sans', system-ui, sans-serif;
}
.block-container { max-width: 1080px; padding-top: 2rem; padding-bottom: 4rem; }
footer { visibility: hidden; }
[data-testid="stHeader"] { background: transparent !important; }
[data-testid="stAppDeployButton"], .stDeployButton { display: none; }
[data-testid="stToolbar"] { visibility: hidden; }
/* ── Hero ── */
.hero {
    display: grid; grid-template-columns: 1.1fr 0.9fr; gap: 2.2rem;
    align-items: center; margin: 0.4rem 0 2rem;
}
.hero-badge {
    display: inline-flex; align-items: center; gap: 0.5rem;
    color: var(--accent); font-size: 0.82rem; font-weight: 600; margin-bottom: 1rem;
}
.hero-badge::before { content: ""; width: 8px; height: 8px; border-radius: 50%; background: var(--accent); }
.hero-title {
    font-family: 'Bricolage Grotesque', sans-serif;
    font-size: 2.9rem; font-weight: 700; line-height: 1.06; letter-spacing: -0.02em;
    margin: 0 0 0.9rem;
}
.hero-sub { color: var(--muted); font-size: 1.02rem; line-height: 1.6; max-width: 44ch; }
.hero-img {
    width: 100%; height: 270px; object-fit: cover; display: block;
    border-radius: 20px; border: 1px solid var(--line);
}
@media (max-width: 800px) {
    .hero { grid-template-columns: 1fr; }
    .hero-title { font-size: 2.1rem; }
    .hero-img { height: 180px; }
}

/* ── Destination tiles ── */
.dest { position: relative; height: 96px; border-radius: 12px; overflow: hidden; }
.dest img { width: 100%; height: 100%; object-fit: cover; filter: brightness(0.62); transition: transform .35s ease; }
.dest:hover img { transform: scale(1.08); }
.dest span {
    position: absolute; left: 12px; bottom: 9px;
    color: #fff; font-size: 0.85rem; font-weight: 600; text-shadow: 0 1px 6px rgba(0,0,0,.5);
}

/* ── Section headers ── */
.sec-head { margin: 2.2rem 0 0.8rem; padding-bottom: 0.5rem; border-bottom: 1px solid var(--line); }
.sec-head span { font-family: 'Bricolage Grotesque', sans-serif; font-size: 1.3rem; font-weight: 700; }
.input-label { font-family: 'Bricolage Grotesque', sans-serif; font-size: 1.15rem; font-weight: 700; margin: 0.4rem 0 0.7rem; }

/* ── Text inputs ── */
[data-baseweb="textarea"], [data-baseweb="base-input"] { border-radius: 12px !important; border-color: var(--line) !important; }
.stTextArea textarea { font-size: 1rem !important; line-height: 1.5 !important; resize: none !important; }
[data-baseweb="textarea"]:focus-within, [data-baseweb="base-input"]:focus-within {
    border-color: var(--accent) !important;
    box-shadow: 0 0 0 3px color-mix(in srgb, var(--accent) 25%, transparent) !important;
}

/* ── Quick-prompt chips (all normal buttons) ── */
div[data-testid="stButton"] > button {
    width: 100%; background: transparent; color: inherit;
    border: 1.5px solid var(--line); border-radius: 999px;
    padding: 0.3rem 0.9rem; min-height: 0; font-size: 0.86rem; font-weight: 500;
    transition: border-color .15s, color .15s, background .15s;
}
div[data-testid="stButton"] > button p { color: inherit; }
div[data-testid="stButton"] > button:hover {
    border-color: var(--accent); color: var(--accent);
    background: color-mix(in srgb, var(--accent) 9%, transparent);
}

/* ── Generate button (the one primary button) ── */
div[data-testid="stButton"] > button[kind="primary"],
div[data-testid="stButton"] > button[data-testid="stBaseButton-primary"] {
    background: var(--accent-deep); color: #fff; border: none; border-radius: 12px;
    padding: 0.85rem 2rem; font-size: 1.05rem; font-weight: 600;
    box-shadow: 0 6px 18px color-mix(in srgb, var(--accent-deep) 40%, transparent);
}
div[data-testid="stButton"] > button[kind="primary"] p,
div[data-testid="stButton"] > button[data-testid="stBaseButton-primary"] p { color: #fff; font-weight: 600; }
div[data-testid="stButton"] > button[kind="primary"]:hover,
div[data-testid="stButton"] > button[data-testid="stBaseButton-primary"]:hover {
    background: #115E59; color: #fff; border: none;
}

/* ── Agent step cards (st.status is an expander) ── */
[data-testid="stExpander"] {
    border: 1px solid var(--line) !important; border-left: 4px solid var(--accent) !important;
    border-radius: 12px !important; background: var(--soft); margin-bottom: 0.7rem;
}
[data-testid="stExpander"] summary { font-weight: 600; }

/* ── Metric bar ── */
.metric-row { display: flex; gap: 0.9rem; margin: 1.4rem 0; }
.metric-box {
    flex: 1; background: var(--soft); border: 1px solid var(--line);
    border-radius: 12px; padding: 0.9rem 1.2rem; text-align: center;
}
.metric-val { font-family: 'Bricolage Grotesque', sans-serif; font-size: 1.7rem; font-weight: 700; color: var(--accent); }
.metric-lbl { font-size: 0.82rem; color: var(--muted); margin-top: 0.15rem; }

/* ── Final plan card (bordered container carrying .plan-anchor) ── */
[data-testid="stVerticalBlockBorderWrapper"]:has(.plan-anchor) {
    border: 1px solid var(--line); border-top: 4px solid var(--accent);
    border-radius: 14px; background: var(--soft); padding: 0.6rem 1.1rem;
}
[data-testid="stVerticalBlockBorderWrapper"]:has(.plan-anchor) p,
[data-testid="stVerticalBlockBorderWrapper"]:has(.plan-anchor) li { line-height: 1.75; }

/* ── Save bar ── */
.save-bar {
    background: var(--soft); border: 1px solid var(--line); border-radius: 10px;
    padding: 0.7rem 1.1rem; color: var(--muted); font-size: 0.88rem;
}
.save-bar code { color: var(--accent); background: transparent; }

/* ── Download button ── */
[data-testid="stDownloadButton"] button {
    width: 100%; background: transparent; color: inherit;
    border: 1.5px solid var(--line); border-radius: 10px; font-weight: 600;
    transition: border-color .15s, color .15s;
}
[data-testid="stDownloadButton"] button p { color: inherit; }
[data-testid="stDownloadButton"] button:hover { border-color: var(--accent); color: var(--accent); }

/* ── Sidebar ── */
.sidebar-title { font-family: 'Bricolage Grotesque', sans-serif; font-size: 1.02rem; font-weight: 700; margin: 1.1rem 0 0.5rem; }
.sidebar-chip {
    background: var(--soft); border: 1px solid var(--line); border-radius: 8px;
    padding: 0.42rem 0.75rem; margin-bottom: 0.4rem; font-size: 0.86rem;
}

@media (prefers-reduced-motion: reduce) { * { transition: none !important; } }
</style>
""", unsafe_allow_html=True)

# ── Sidebar ───────────────────────────────────────────────────────────────────
with st.sidebar:
    st.markdown("<div class='sidebar-title'>🌍 AI Travel Planner</div>", unsafe_allow_html=True)
    st.markdown("---")

    thread_id = st.text_input(" User ID", value="neha_user",
                              help="Your session ID — keeps travel history across queries")

    st.markdown("<div class='sidebar-title'>Powered by</div>", unsafe_allow_html=True)
    for tech in ["🔗 LangGraph", "🧠 Groq · LLaMA 3.3 70B", "🐘 PostgreSQL", "🔍 Tavily Search", "✈️ AviationStack"]:
        st.markdown(f"<div class='sidebar-chip'>{tech}</div>", unsafe_allow_html=True)

    st.markdown("<div class='sidebar-title'>Agent Pipeline</div>", unsafe_allow_html=True)
    for step in ["① Flight Agent", "② Hotel Agent", "③ Itinerary Agent", "④ Final Agent"]:
        st.markdown(f"<div class='sidebar-chip'>{step}</div>", unsafe_allow_html=True)

# ── Hero ──────────────────────────────────────────────────────────────────────
st.markdown("""
<div class="hero">
    <div>
        <div class="hero-badge">Multi-Agent AI System</div>
        <div class="hero-title">✈️ AI Travel Booking System</div>
        <div class="hero-sub">Four specialized agents work together — searching flights, hotels, building an itinerary, and delivering your perfect trip plan.</div>
    </div>
    <img class="hero-img"
         src="https://images.unsplash.com/photo-1436491865332-7a61a109cc05?w=1000&q=80"
         alt="airplane above clouds"/>
</div>
""", unsafe_allow_html=True)

# ── Destination image strip ───────────────────────────────────────────────────
DESTINATIONS = [
    ("🇯🇵 Tokyo",     "https://images.unsplash.com/photo-1540959733332-eab4deabeeaf?w=400&q=70"),
    ("🇫🇷 Paris",     "https://images.unsplash.com/photo-1502602898657-3e91760cbb34?w=400&q=70"),
    ("🇹🇭 Bangkok",   "https://images.unsplash.com/photo-1508009603885-50cf7c579365?w=400&q=70"),
    ("🇮🇹 Rome",      "https://images.unsplash.com/photo-1552832230-c0197dd311b5?w=400&q=70"),
    ("🇦🇪 Dubai",     "https://images.unsplash.com/photo-1512453979798-5ea266f8880c?w=400&q=70"),
]

cols = st.columns(5)
for col, (name, img_url) in zip(cols, DESTINATIONS):
    with col:
        st.markdown(f"""
        <div class="dest">
            <img src="{img_url}" alt="{name}" />
            <span>{name}</span>
        </div>
        """, unsafe_allow_html=True)

st.markdown("<br>", unsafe_allow_html=True)

# ── Input ─────────────────────────────────────────────────────────────────────
st.markdown("<div class='input-label'>🗺️ Describe your trip</div>", unsafe_allow_html=True)

QUICK = ["7-day Japan under ₹2L", "Paris trip for 5 days", "Dubai weekend trip", "Bali backpacking 10 days"]
qcols = st.columns(len(QUICK))
quick_fill = ""
for qc, label in zip(qcols, QUICK):
    with qc:
        if st.button(label, key=f"q_{label}"):
            quick_fill = label

user_query = st.text_area(
    "Trip description",
    value=quick_fill,
    placeholder="e.g. Plan a complete 7-day Japan trip including flights, hotels and sightseeing under ₹2 lakhs",
    height=100,
    label_visibility="collapsed",
)

generate = st.button("🚀  Generate My Travel Plan", use_container_width=True, type="primary")

# ── Agent pipeline ────────────────────────────────────────────────────────────
AGENT_META = {
    "flight_agent":    ("✈️", "Flight Agent"),
    "hotel_agent":     ("🏨", "Hotel Agent"),
    "itinerary_agent": ("🗓️", "Itinerary Agent"),
    "final_agent":     ("🧠", "Final Agent"),
}

def show_outputs(r):
    """Metrics, final plan card and download buttons (used live and when redrawn)."""
    # Metrics
    st.markdown(f"""
    <div class="metric-row">
        <div class="metric-box"><div class="metric-val">4</div><div class="metric-lbl">Agents Run</div></div>
        <div class="metric-box"><div class="metric-val">{r['llm_calls']}</div><div class="metric-lbl">LLM Calls</div></div>
        <div class="metric-box"><div class="metric-val">✅</div><div class="metric-lbl">Status</div></div>
    </div>
    """, unsafe_allow_html=True)

    # Final plan card
    if r["final_response"]:
        st.markdown("<div class='sec-head'><span>🧠 Final Travel Plan</span></div>",
                    unsafe_allow_html=True)
        with st.container(border=True):
            st.markdown("<span class='plan-anchor'></span>", unsafe_allow_html=True)
            st.markdown(r["final_response"])

    # PDF download
    if r["pdf_bytes"]:
        st.download_button(
            "📄 Download as PDF",
            data=r["pdf_bytes"],
            file_name=r["filename"].replace(".md", ".pdf"),
            mime="application/pdf",
            use_container_width=True,
        )
    else:
        st.caption("PDF export needs `fpdf2` (pip install fpdf2)")


if generate:
    if not user_query.strip():
        st.warning("Please describe your trip first.")
    else:
        config = {"configurable": {"thread_id": thread_id}}
        collected = {"flight_results": "", "hotel_results": "",
                     "itinerary": "", "final_response": "", "llm_calls": 0}

        st.markdown("---")
        st.markdown("<div class='sec-head'><span>🤖 Agent Pipeline — Live</span></div>",
                    unsafe_allow_html=True)

        for chunk in app.stream(
            {
                "messages": [HumanMessage(content=user_query)],
                "user_query": user_query,
                "flight_results": "",
                "hotel_results": "",
                "itinerary": "",
                "llm_calls": 0,
            },
            config=config,
            stream_mode="updates",
        ):
            for node_name, state_update in chunk.items():
                icon, label = AGENT_META.get(node_name, ("🔧", node_name))

                with st.status(f"{icon}  {label}", state="complete", expanded=True):
                    if node_name == "flight_agent":
                        text = state_update.get("flight_results", "")
                        collected["flight_results"] = text
                        st.markdown(text or "_No flight data returned._")

                    elif node_name == "hotel_agent":
                        text = state_update.get("hotel_results", "")
                        collected["hotel_results"] = text
                        st.markdown(text or "_No hotel data returned._")

                    elif node_name == "itinerary_agent":
                        text = state_update.get("itinerary", "")
                        collected["itinerary"] = text
                        st.markdown(text or "_No itinerary generated._")

                    elif node_name == "final_agent":
                        msgs = state_update.get("messages", [])
                        text = msgs[-1].content if msgs else ""
                        collected["final_response"] = text
                        st.markdown(text or "_No final response._")

                    collected["llm_calls"] = state_update.get("llm_calls", collected["llm_calls"])

        # Save
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        filename = f"travel_plan_{timestamp}.md"
        save_dir = os.path.join(os.path.dirname(__file__), "travel_plans")
        os.makedirs(save_dir, exist_ok=True)

        file_content = f"""# Travel Plan
**Query:** {user_query}
**Generated:** {datetime.now().strftime("%Y-%m-%d %H:%M:%S")}
**User ID:** {thread_id}

---

## ✈️ Flight Information
{collected['flight_results'] or 'N/A'}

---

## 🏨 Hotel Information
{collected['hotel_results'] or 'N/A'}

---

## 🗓️ Itinerary
{collected['itinerary'] or 'N/A'}

---

## 🧠 Final Travel Plan
{collected['final_response'] or 'N/A'}

---
*LLM Calls: {collected['llm_calls']}*
"""
        with open(os.path.join(save_dir, filename), "w", encoding="utf-8") as f:
            f.write(file_content)

        # PDF version of the same plan
        try:
            pdf_bytes = build_pdf(
                user_query, thread_id, datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                [("Flight Information", collected["flight_results"]),
                 ("Hotel Information", collected["hotel_results"]),
                 ("Itinerary", collected["itinerary"]),
                 ("Final Travel Plan", collected["final_response"])],
                collected["llm_calls"],
            )
        except Exception:
            pdf_bytes = None

        result = {**collected, "filename": filename, "pdf_bytes": pdf_bytes}
        show_outputs(result)