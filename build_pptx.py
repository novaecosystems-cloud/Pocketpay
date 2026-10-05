import os
from pptx import Presentation
from pptx.util import Inches, Pt
from pptx.dml.color import RGBColor
from pptx.enum.text import PP_ALIGN, MSO_ANCHOR
from pptx.enum.shapes import MSO_SHAPE

out_pptx = r"C:\Users\Shourya\.gemini\antigravity\scratch\dark-factory\Pocketpay_Pitch_Deck.pptx"
img_moat = r"C:\Users\Shourya\.gemini\antigravity\scratch\dark-factory\pocketpay_elevenlabs_moat.png"
img_stats = r"C:\Users\Shourya\.gemini\antigravity\scratch\dark-factory\pocketpay_elevenlabs_stats.png"

# Color Palette (ElevenLabs Dark Obsidian)
COLOR_BG = RGBColor(5, 7, 13)         # #05070D
COLOR_CARD = RGBColor(11, 15, 25)     # #0B0F19
COLOR_CARD_BORDER = RGBColor(30, 41, 59) # #1E293B
COLOR_WHITE = RGBColor(255, 255, 255) # #FFFFFF
COLOR_SUB = RGBColor(148, 163, 184)   # #94A3B8
COLOR_MUTED = RGBColor(100, 116, 139) # #64748B

COLOR_CYAN = RGBColor(0, 240, 255)    # #00F0FF
COLOR_PURPLE = RGBColor(139, 92, 246) # #8B5CF6
COLOR_EMERALD = RGBColor(16, 185, 129)# #10B981
COLOR_ROSE = RGBColor(244, 63, 94)    # #F43F5E
COLOR_AMBER = RGBColor(245, 158, 11)  # #F59E0B

prs = Presentation()
prs.slide_width = Inches(13.333)
prs.slide_height = Inches(7.5)
blank_layout = prs.slide_layouts[6]

def set_slide_background(slide):
    bg_shape = slide.shapes.add_shape(MSO_SHAPE.RECTANGLE, 0, 0, Inches(13.333), Inches(7.5))
    bg_shape.fill.solid()
    bg_shape.fill.fore_color.rgb = COLOR_BG
    bg_shape.line.fill.background()
    return bg_shape

def add_header(slide, badge_text, badge_color, title_text, subtitle_text, slide_num):
    # Badge
    badge_box = slide.shapes.add_textbox(Inches(0.8), Inches(0.4), Inches(8), Inches(0.4))
    tf_b = badge_box.text_frame
    tf_b.word_wrap = True
    tf_b.margin_left = tf_b.margin_right = tf_b.margin_top = tf_b.margin_bottom = 0
    p_b = tf_b.paragraphs[0]
    p_b.text = badge_text.upper()
    p_b.font.size = Pt(10)
    p_b.font.bold = True
    p_b.font.color.rgb = badge_color

    # Title & Subtitle
    title_box = slide.shapes.add_textbox(Inches(0.8), Inches(0.75), Inches(10.5), Inches(1.1))
    tf_t = title_box.text_frame
    tf_t.word_wrap = True
    tf_t.margin_left = tf_t.margin_right = tf_t.margin_top = tf_t.margin_bottom = 0
    
    p_t = tf_t.paragraphs[0]
    p_t.text = title_text
    p_t.font.size = Pt(26)
    p_t.font.bold = True
    p_t.font.color.rgb = COLOR_WHITE
    
    if subtitle_text:
        p_s = tf_t.add_paragraph()
        p_s.text = subtitle_text
        p_s.font.size = Pt(13)
        p_s.font.color.rgb = COLOR_SUB
        p_s.space_before = Pt(4)

    # Number
    num_box = slide.shapes.add_textbox(Inches(11.5), Inches(0.5), Inches(1.0), Inches(0.4))
    tf_n = num_box.text_frame
    tf_n.margin_left = tf_n.margin_right = tf_n.margin_top = tf_n.margin_bottom = 0
    p_n = tf_n.paragraphs[0]
    p_n.alignment = PP_ALIGN.RIGHT
    p_n.text = f"{slide_num:02d} / 10"
    p_n.font.size = Pt(12)
    p_n.font.bold = True
    p_n.font.color.rgb = COLOR_MUTED

def add_footer(slide, left_text="WeAreDevelopers × BAND Hackathon 2026", right_text="github.com/novaecosystems-cloud/Pocketpay"):
    foot_box = slide.shapes.add_textbox(Inches(0.8), Inches(6.9), Inches(11.733), Inches(0.35))
    tf = foot_box.text_frame
    tf.word_wrap = True
    tf.margin_left = tf.margin_right = tf.margin_top = tf.margin_bottom = 0
    p = tf.paragraphs[0]
    p.text = left_text
    p.font.size = Pt(10)
    p.font.color.rgb = COLOR_MUTED
    
    run_r = p.add_run()
    run_r.text = f"                                                                               {right_text}"
    run_r.font.size = Pt(10)
    run_r.font.color.rgb = COLOR_MUTED

def add_card(slide, left, top, width, height, border_top_color=COLOR_CYAN):
    card = slide.shapes.add_shape(MSO_SHAPE.ROUNDED_RECTANGLE, left, top, width, height)
    card.fill.solid()
    card.fill.fore_color.rgb = COLOR_CARD
    card.line.color.rgb = COLOR_CARD_BORDER
    card.line.width = Pt(1)
    
    # Top accent line
    accent = slide.shapes.add_shape(MSO_SHAPE.RECTANGLE, left + Inches(0.1), top, width - Inches(0.2), Inches(0.05))
    accent.fill.solid()
    accent.fill.fore_color.rgb = border_top_color
    accent.line.fill.background()
    return card

# -------------------------------------------------------------
# SLIDE 1: COVER
# -------------------------------------------------------------
s1 = prs.slides.add_slide(blank_layout)
set_slide_background(s1)

tb1 = s1.shapes.add_textbox(Inches(1.5), Inches(1.4), Inches(10.333), Inches(4.5))
tf1 = tb1.text_frame
tf1.word_wrap = True
tf1.vertical_anchor = MSO_ANCHOR.MIDDLE

p_badge = tf1.paragraphs[0]
p_badge.alignment = PP_ALIGN.CENTER
p_badge.text = "AI DARK FACTORY • HACKATHON EDITION (TRACK 2: POCKETFUL)"
p_badge.font.size = Pt(12)
p_badge.font.bold = True
p_badge.font.color.rgb = COLOR_PURPLE

p_title = tf1.add_paragraph()
p_title.alignment = PP_ALIGN.CENTER
p_title.text = "POCKETPAY"
p_title.font.size = Pt(64)
p_title.font.bold = True
p_title.font.color.rgb = COLOR_WHITE
p_title.space_before = Pt(10)

p_sub = tf1.add_paragraph()
p_sub.alignment = PP_ALIGN.CENTER
p_sub.text = "A Sovereign, Bitemporal Double-Entry Financial Wallet\nEngineered by an Autonomous 3-Seat Coding Factory"
p_sub.font.size = Pt(18)
p_sub.font.color.rgb = COLOR_SUB
p_sub.space_before = Pt(10)

p_pills = tf1.add_paragraph()
p_pills.alignment = PP_ALIGN.CENTER
p_pills.text = "★ 100% PERFECT SCORE (193/193 CHECKS)   •   STAGE 4 CERTIFIED (SCORE: 1.00)   •   ZERO OVERSHOOT"
p_pills.font.size = Pt(11)
p_pills.font.bold = True
p_pills.font.color.rgb = COLOR_CYAN
p_pills.space_before = Pt(30)

add_footer(s1)

# -------------------------------------------------------------
# SLIDE 2: THE PROBLEM
# -------------------------------------------------------------
s2 = prs.slides.add_slide(blank_layout)
set_slide_background(s2)
add_header(s2, "The Industry Problem", COLOR_ROSE, "Fragile Financial Ledgers Under Concurrency", "Why modern mobile wallets break during concurrent spikes and network retries", 2)

card_w = Inches(3.64)
card_h = Inches(4.5)
y_top = Inches(2.1)

# Card 1
add_card(s2, Inches(0.8), y_top, card_w, card_h, COLOR_ROSE)
tb = s2.shapes.add_textbox(Inches(1.05), y_top + Inches(0.3), card_w - Inches(0.5), card_h - Inches(0.6))
tf = tb.text_frame
tf.word_wrap = True
p = tf.paragraphs[0]
p.text = "17.4%"
p.font.size = Pt(36)
p.font.bold = True
p.font.color.rgb = COLOR_ROSE
p2 = tf.add_paragraph()
p2.text = "FLOATING-POINT DRIFT"
p2.font.size = Pt(11)
p2.font.bold = True
p2.font.color.rgb = COLOR_WHITE
p2.space_before = Pt(4)
p3 = tf.add_paragraph()
p3.text = "Standard software represents currency as floating-point decimals (0.1 + 0.2 = 0.30000000000000004).\n\nUnder high-frequency transfer bursts, rounding errors compound into critical balance discrepancies and phantom money creation."
p3.font.size = Pt(12)
p3.font.color.rgb = COLOR_SUB
p3.space_before = Pt(14)

# Card 2
add_card(s2, Inches(4.84), y_top, card_w, card_h, COLOR_ROSE)
tb = s2.shapes.add_textbox(Inches(5.09), y_top + Inches(0.3), card_w - Inches(0.5), card_h - Inches(0.6))
tf = tb.text_frame
tf.word_wrap = True
p = tf.paragraphs[0]
p.text = "2x Double"
p.font.size = Pt(36)
p.font.bold = True
p.font.color.rgb = COLOR_ROSE
p2 = tf.add_paragraph()
p2.text = "CHARGES ON PACKET LOSS"
p2.font.size = Pt(11)
p2.font.bold = True
p2.font.color.rgb = COLOR_WHITE
p2.space_before = Pt(4)
p3 = tf.add_paragraph()
p3.text = "Mobile connections drop packets when users tap 'Pay'.\n\nWithout distributed cryptographic idempotency tracking across all write paths, client retries trigger duplicate withdrawals, overdrafting user accounts."
p3.font.size = Pt(12)
p3.font.color.rgb = COLOR_SUB
p3.space_before = Pt(14)

# Card 3
add_card(s2, Inches(8.88), y_top, card_w, card_h, COLOR_ROSE)
tb = s2.shapes.add_textbox(Inches(9.13), y_top + Inches(0.3), card_w - Inches(0.5), card_h - Inches(0.6))
tf = tb.text_frame
tf.word_wrap = True
p = tf.paragraphs[0]
p.text = "Destructive"
p.font.size = Pt(36)
p.font.bold = True
p.font.color.rgb = COLOR_ROSE
p2 = tf.add_paragraph()
p2.text = "MUTABLE OVERWRITES"
p2.font.size = Pt(11)
p2.font.bold = True
p2.font.color.rgb = COLOR_WHITE
p2.space_before = Pt(4)
p3 = tf.add_paragraph()
p3.text = "Single-table ledgers mutate rows in-place (UPDATE balance SET amount = ...), destroying audit trails.\n\nWhen a retroactive correction occurs, it is mathematically impossible to reconstruct what the ledger knew at a specific past point in time."
p3.font.size = Pt(12)
p3.font.color.rgb = COLOR_SUB
p3.space_before = Pt(14)

add_footer(s2)

# -------------------------------------------------------------
# SLIDE 3: THE SOLUTION
# -------------------------------------------------------------
s3 = prs.slides.add_slide(blank_layout)
set_slide_background(s3)
add_header(s3, "The Pocketpay Solution", COLOR_CYAN, "Mathematical Conservation & Bitemporality", "Architected from first principles to guarantee sovereign financial correctness", 3)

# Card 1
add_card(s3, Inches(0.8), y_top, card_w, card_h, COLOR_CYAN)
tb = s3.shapes.add_textbox(Inches(1.05), y_top + Inches(0.3), card_w - Inches(0.5), card_h - Inches(0.6))
tf = tb.text_frame
tf.word_wrap = True
p = tf.paragraphs[0]
p.text = "sum(Δ) = 0"
p.font.size = Pt(34)
p.font.bold = True
p.font.color.rgb = COLOR_CYAN
p2 = tf.add_paragraph()
p2.text = "DOUBLE-ENTRY CONSERVATION"
p2.font.size = Pt(11)
p2.font.bold = True
p2.font.color.rgb = COLOR_WHITE
p2.space_before = Pt(4)
p3 = tf.add_paragraph()
p3.text = "Every transaction strictly debits the sender and credits the receiver in atomic transactions.\n\nEnforced directly at the SQL engine level with CHECK (balance >= 0) to guarantee money is neither created nor destroyed."
p3.font.size = Pt(12)
p3.font.color.rgb = COLOR_SUB
p3.space_before = Pt(14)

# Card 2
add_card(s3, Inches(4.84), y_top, card_w, card_h, COLOR_CYAN)
tb = s3.shapes.add_textbox(Inches(5.09), y_top + Inches(0.3), card_w - Inches(0.5), card_h - Inches(0.6))
tf = tb.text_frame
tf.word_wrap = True
p = tf.paragraphs[0]
p.text = "Dual-Time"
p.font.size = Pt(34)
p.font.bold = True
p.font.color.rgb = COLOR_CYAN
p2 = tf.add_paragraph()
p2.text = "BITEMPORAL REVISIONS"
p2.font.size = Pt(11)
p2.font.bold = True
p2.font.color.rgb = COLOR_WHITE
p2.space_before = Pt(4)
p3 = tf.add_paragraph()
p3.text = "Separates Effective Time (as_of / real world) from Recorded Time (known_at / system recording).\n\nEnables perfect historical balance reconstruction and auditable retroactive amendments via immutable payment_revisions."
p3.font.size = Pt(12)
p3.font.color.rgb = COLOR_SUB
p3.space_before = Pt(14)

# Card 3
add_card(s3, Inches(8.88), y_top, card_w, card_h, COLOR_CYAN)
tb = s3.shapes.add_textbox(Inches(9.13), y_top + Inches(0.3), card_w - Inches(0.5), card_h - Inches(0.6))
tf = tb.text_frame
tf.word_wrap = True
p = tf.paragraphs[0]
p.text = "Zero Drift"
p.font.size = Pt(34)
p.font.bold = True
p.font.color.rgb = COLOR_CYAN
p2 = tf.add_paragraph()
p2.text = "INTEGER MINOR UNITS & KEYS"
p2.font.size = Pt(11)
p2.font.bold = True
p2.font.color.rgb = COLOR_WHITE
p2.space_before = Pt(4)
p3 = tf.add_paragraph()
p3.text = "All balances and amounts are stored as whole integer minor units (cents / 100).\n\n5-path distributed idempotency engine tracks Idempotency-Key headers with canonical payload hashes for safe client retries."
p3.font.size = Pt(12)
p3.font.color.rgb = COLOR_SUB
p3.space_before = Pt(14)

add_footer(s3)

# -------------------------------------------------------------
# SLIDE 4: THE AUTONOMOUS FACTORY
# -------------------------------------------------------------
s4 = prs.slides.add_slide(blank_layout)
set_slide_background(s4)
add_header(s4, "Autonomous Factory", COLOR_PURPLE, "The 3-Seat Band Desktop Factory", "Multi-agent collaboration recorded and verified in room.json without human steering", 4)

# Card 1: Architect
add_card(s4, Inches(0.8), y_top, card_w, card_h, COLOR_PURPLE)
tb = s4.shapes.add_textbox(Inches(1.05), y_top + Inches(0.3), card_w - Inches(0.5), card_h - Inches(0.6))
tf = tb.text_frame
tf.word_wrap = True
p = tf.paragraphs[0]
p.text = "@architect"
p.font.size = Pt(28)
p.font.bold = True
p.font.color.rgb = COLOR_PURPLE
p2 = tf.add_paragraph()
p2.text = "LEAD COORDINATOR & PLANNER"
p2.font.size = Pt(10)
p2.font.bold = True
p2.font.color.rgb = COLOR_WHITE
p2.space_before = Pt(4)
p3 = tf.add_paragraph()
p3.text = "• Deconstructs specifications into atomic, verifiable tasks\n• Defines schema contracts and balance invariants\n• Enforces strict stage boundaries to prevent overshooting\n• Dispatches scoped implementation assignments"
p3.font.size = Pt(12)
p3.font.color.rgb = COLOR_SUB
p3.space_before = Pt(14)

# Card 2: Implementer
add_card(s4, Inches(4.84), y_top, card_w, card_h, COLOR_PURPLE)
tb = s4.shapes.add_textbox(Inches(5.09), y_top + Inches(0.3), card_w - Inches(0.5), card_h - Inches(0.6))
tf = tb.text_frame
tf.word_wrap = True
p = tf.paragraphs[0]
p.text = "@implementer"
p.font.size = Pt(28)
p.font.bold = True
p.font.color.rgb = COLOR_PURPLE
p2 = tf.add_paragraph()
p2.text = "SYSTEMS & BACKEND ENGINEER"
p2.font.size = Pt(10)
p2.font.bold = True
p2.font.color.rgb = COLOR_WHITE
p2.space_before = Pt(4)
p3 = tf.add_paragraph()
p3.text = "• Builds modular FastAPI services in stage-N/app/\n• Implements SQLite WAL mode with 30s busy timeout\n• Writes Dockerfiles and executable RUN.md guides\n• Commits clean git revisions with traceable SHAs"
p3.font.size = Pt(12)
p3.font.color.rgb = COLOR_SUB
p3.space_before = Pt(14)

# Card 3: Reviewer
add_card(s4, Inches(8.88), y_top, card_w, card_h, COLOR_PURPLE)
tb = s4.shapes.add_textbox(Inches(9.13), y_top + Inches(0.3), card_w - Inches(0.5), card_h - Inches(0.6))
tf = tb.text_frame
tf.word_wrap = True
p = tf.paragraphs[0]
p.text = "@reviewer"
p.font.size = Pt(28)
p.font.bold = True
p.font.color.rgb = COLOR_PURPLE
p2 = tf.add_paragraph()
p2.text = "QA & COMPLIANCE AUDITOR"
p2.font.size = Pt(10)
p2.font.bold = True
p2.font.color.rgb = COLOR_WHITE
p2.space_before = Pt(4)
p3 = tf.add_paragraph()
p3.text = "• Runs official harness test suites across all stages\n• Audits Gate 4 vocabulary compliance (0 leaks)\n• Stress-tests edge cases (concurrency, overdrafts)\n• Formally certifies milestones upon passing checks"
p3.font.size = Pt(12)
p3.font.color.rgb = COLOR_SUB
p3.space_before = Pt(14)

add_footer(s4)

# -------------------------------------------------------------
# SLIDE 5: 4-STAGE PROGRESSIVE DELIVERY
# -------------------------------------------------------------
s5 = prs.slides.add_slide(blank_layout)
set_slide_background(s5)
add_header(s5, "Progressive Delivery", COLOR_EMERALD, "Four Progressive Stages Without Overshoot", "Each stage is an isolated, complete containerized service extending previous capabilities", 5)

card4_w = Inches(2.72)
# Stage 1
add_card(s5, Inches(0.8), y_top, card4_w, card_h, COLOR_EMERALD)
tb = s5.shapes.add_textbox(Inches(0.95), y_top + Inches(0.2), card4_w - Inches(0.3), card_h - Inches(0.4))
tf = tb.text_frame
tf.word_wrap = True
p = tf.paragraphs[0]
p.text = "Stage 1"
p.font.size = Pt(26)
p.font.bold = True
p.font.color.rgb = COLOR_EMERALD
p2 = tf.add_paragraph()
p2.text = "CORE LEDGER"
p2.font.size = Pt(10)
p2.font.bold = True
p2.font.color.rgb = COLOR_WHITE
p3 = tf.add_paragraph()
p3.text = "• Double-entry balance\n• SQLite WAL mode\n• PBKDF2 authentication\n• 5-path idempotency\n• P2P transfers & splits\n\n147/147 Checks (100%)"
p3.font.size = Pt(11)
p3.font.color.rgb = COLOR_SUB
p3.space_before = Pt(10)

# Stage 2
add_card(s5, Inches(3.82), y_top, card4_w, card_h, COLOR_EMERALD)
tb = s5.shapes.add_textbox(Inches(3.97), y_top + Inches(0.2), card4_w - Inches(0.3), card_h - Inches(0.4))
tf = tb.text_frame
tf.word_wrap = True
p = tf.paragraphs[0]
p.text = "Stage 2"
p.font.size = Pt(26)
p.font.bold = True
p.font.color.rgb = COLOR_EMERALD
p2 = tf.add_paragraph()
p2.text = "HOLDS & SSR UI"
p2.font.size = Pt(10)
p2.font.bold = True
p2.font.color.rgb = COLOR_WHITE
p3 = tf.add_paragraph()
p3.text = "• Two-phase authorizations\n• Dynamic balance holds\n• Available = Total - Held\n• SSR HTML interface\n• data-testid selectors\n\n35/35 Checks (100%)"
p3.font.size = Pt(11)
p3.font.color.rgb = COLOR_SUB
p3.space_before = Pt(10)

# Stage 3
add_card(s5, Inches(6.84), y_top, card4_w, card_h, COLOR_EMERALD)
tb = s5.shapes.add_textbox(Inches(6.99), y_top + Inches(0.2), card4_w - Inches(0.3), card_h - Inches(0.4))
tf = tb.text_frame
tf.word_wrap = True
p = tf.paragraphs[0]
p.text = "Stage 3"
p.font.size = Pt(26)
p.font.bold = True
p.font.color.rgb = COLOR_EMERALD
p2 = tf.add_paragraph()
p2.text = "BITEMPORAL REVISIONS"
p2.font.size = Pt(10)
p2.font.bold = True
p2.font.color.rgb = COLOR_WHITE
p3 = tf.add_paragraph()
p3.text = "• as_of & known_at\n• [from, to) statements\n• Snapshot pagination\n• Retroactive corrections\n• Overdraft protection\n\n6/6 Checks (100%)"
p3.font.size = Pt(11)
p3.font.color.rgb = COLOR_SUB
p3.space_before = Pt(10)

# Stage 4
add_card(s5, Inches(9.86), y_top, card4_w, card_h, COLOR_EMERALD)
tb = s5.shapes.add_textbox(Inches(10.01), y_top + Inches(0.2), card4_w - Inches(0.3), card_h - Inches(0.4))
tf = tb.text_frame
tf.word_wrap = True
p = tf.paragraphs[0]
p.text = "Stage 4"
p.font.size = Pt(26)
p.font.bold = True
p.font.color.rgb = COLOR_EMERALD
p2 = tf.add_paragraph()
p2.text = "CLEARING & REFUNDS"
p2.font.size = Pt(10)
p2.font.bold = True
p2.font.color.rgb = COLOR_WHITE
p3 = tf.add_paragraph()
p3.text = "• Receiver refunds\n• 32-item operator batch\n• Settlement completeness\n• Shared recorded_at\n• Linked immutability\n\n5/5 Checks (100%)"
p3.font.size = Pt(11)
p3.font.color.rgb = COLOR_SUB
p3.space_before = Pt(10)

add_footer(s5)

# -------------------------------------------------------------
# SLIDE 6: EVALUATION SCORECARD
# -------------------------------------------------------------
s6 = prs.slides.add_slide(blank_layout)
set_slide_background(s6)
add_header(s6, "Evaluation Scorecard", COLOR_CYAN, "100% Perfect Score Across 193 Checks", "Official automated test verification results from the Dark Factory test harness", 6)

if os.path.exists(img_stats):
    s6.shapes.add_picture(img_stats, Inches(0.8), Inches(1.9), Inches(11.733), Inches(4.7))
else:
    tb = s6.shapes.add_textbox(Inches(1.0), Inches(2.5), Inches(11), Inches(3))
    tb.text_frame.text = "193 / 193 Checks Passed (100%)"

add_footer(s6)

# -------------------------------------------------------------
# SLIDE 7: BITEMPORAL MOAT
# -------------------------------------------------------------
s7 = prs.slides.add_slide(blank_layout)
set_slide_background(s7)
add_header(s7, "Technical Moat", COLOR_PURPLE, "Dual-Timeline Accounting Architecture", "Reconstructing historical state with cryptographic fidelity while blocking retroactive overdrafts", 7)

if os.path.exists(img_moat):
    s7.shapes.add_picture(img_moat, Inches(0.8), Inches(1.9), Inches(11.733), Inches(4.7))
else:
    tb = s7.shapes.add_textbox(Inches(1.0), Inches(2.5), Inches(11), Inches(3))
    tb.text_frame.text = "The Pocketpay Bitemporal Moat"

add_footer(s7)

# -------------------------------------------------------------
# SLIDE 8: PRODUCT & LIVE DEMO
# -------------------------------------------------------------
s8 = prs.slides.add_slide(blank_layout)
set_slide_background(s8)
add_header(s8, "Live Product Demo", COLOR_EMERALD, "Interactive Web Interface & Swagger APIs", "Running live on port 8000 with pre-seeded demo accounts ready for screen recording", 8)

add_card(s8, Inches(0.8), y_top, card_w, card_h, COLOR_EMERALD)
tb = s8.shapes.add_textbox(Inches(1.05), y_top + Inches(0.25), card_w - Inches(0.5), card_h - Inches(0.5))
tf = tb.text_frame
tf.word_wrap = True
p = tf.paragraphs[0]
p.text = "Wallet Dashboard"
p.font.size = Pt(20)
p.font.bold = True
p.font.color.rgb = COLOR_EMERALD
p2 = tf.add_paragraph()
p2.text = "REAL-TIME BALANCES & ACTIVITY"
p2.font.size = Pt(9.5)
p2.font.bold = True
p2.font.color.rgb = COLOR_WHITE
p3 = tf.add_paragraph()
p3.text = "• Dynamic balance: Total, Available, Held\n• Instant P2P payments by @handle\n• Transaction activity feed with private notes\n• High-concurrency SQLite WAL ledger\n• Export state & system reset endpoints\n• Automated test-friendly data-testid tags"
p3.font.size = Pt(11)
p3.font.color.rgb = COLOR_SUB
p3.space_before = Pt(10)

add_card(s8, Inches(4.84), y_top, card_w, card_h, COLOR_EMERALD)
tb = s8.shapes.add_textbox(Inches(5.09), y_top + Inches(0.25), card_w - Inches(0.5), card_h - Inches(0.5))
tf = tb.text_frame
tf.word_wrap = True
p = tf.paragraphs[0]
p.text = "Requests & Holds"
p.font.size = Pt(20)
p.font.bold = True
p.font.color.rgb = COLOR_EMERALD
p2 = tf.add_paragraph()
p2.text = "P2P BILL SHARING & TWO-PHASE APIS"
p2.font.size = Pt(9.5)
p2.font.bold = True
p2.font.color.rgb = COLOR_WHITE
p3 = tf.add_paragraph()
p3.text = "• P2P requests: Pay, Decline, and Cancel\n• Multi-party bill splits with remainder handling\n• Merchant authorizations: 2-phase holds\n• Full & partial capture, release/void\n• Interactive OpenAPI Swagger at /docs\n• Strict RFC 3339 timezone validation"
p3.font.size = Pt(11)
p3.font.color.rgb = COLOR_SUB
p3.space_before = Pt(10)

add_card(s8, Inches(8.88), y_top, card_w, card_h, COLOR_CYAN)
tb = s8.shapes.add_textbox(Inches(9.05), y_top + Inches(0.2), card_w - Inches(0.35), card_h - Inches(0.35))
tf = tb.text_frame
tf.word_wrap = True
p = tf.paragraphs[0]
p.text = "🔑 Demo Accounts"
p.font.size = Pt(20)
p.font.bold = True
p.font.color.rgb = COLOR_CYAN
p2 = tf.add_paragraph()
p2.text = "LOGIN PASSWORDS & BALANCES"
p2.font.size = Pt(9.5)
p2.font.bold = True
p2.font.color.rgb = COLOR_WHITE

pa = tf.add_paragraph()
pa.text = "Alice Demo (@alice) • €1,500.00"
pa.font.size = Pt(11)
pa.font.bold = True
pa.font.color.rgb = COLOR_CYAN
pa.space_before = Pt(6)
pad = tf.add_paragraph()
pad.text = "Email: alice@demo.com | Pass: Password123!\nAvail: €1,455.00 | Held: €45.00 (Active hold)"
pad.font.size = Pt(9.5)
pad.font.color.rgb = COLOR_SUB

pb = tf.add_paragraph()
pb.text = "Bob Demo (@bob) • €850.00"
pb.font.size = Pt(11)
pb.font.bold = True
pb.font.color.rgb = COLOR_EMERALD
pb.space_before = Pt(6)
pbd = tf.add_paragraph()
pbd.text = "Email: bob@demo.com | Pass: Password123!\nSent €25.00 dinner, pending concert split"
pbd.font.size = Pt(9.5)
pbd.font.color.rgb = COLOR_SUB

pc = tf.add_paragraph()
pc.text = "Carol Demo (@carol) • €500.00"
pc.font.size = Pt(11)
pc.font.bold = True
pc.font.color.rgb = COLOR_AMBER
pc.space_before = Pt(6)
pcd = tf.add_paragraph()
pcd.text = "Email: carol@demo.com | Pass: Password123!\nFresh wallet, incoming €18.00 coffee request"
pcd.font.size = Pt(9.5)
pcd.font.color.rgb = COLOR_SUB

pfn = tf.add_paragraph()
pfn.text = "⚡ Click-to-fill enabled in UI login side box"
pfn.font.size = Pt(9.5)
pfn.font.bold = True
pfn.font.color.rgb = COLOR_CYAN
pfn.space_before = Pt(6)

add_footer(s8, "Live Vercel Demo: https://temporary-turbo-carbon-owriola.vercel.app/login", "Auto-fill side box enabled • All Demo Passwords: Password123!")

# -------------------------------------------------------------
# SLIDE 9: MARKET OPPORTUNITY
# -------------------------------------------------------------
s9 = prs.slides.add_slide(blank_layout)
set_slide_background(s9)
add_header(s9, "Market Opportunity", COLOR_AMBER, "Autonomous Agent & Embedded FinTech TAM", "Capitalizing on the convergence of AI agents and deterministic financial infrastructure", 9)

add_card(s9, Inches(0.8), y_top, card_w, card_h, COLOR_AMBER)
tb = s9.shapes.add_textbox(Inches(1.05), y_top + Inches(0.3), card_w - Inches(0.5), card_h - Inches(0.6))
tf = tb.text_frame
tf.word_wrap = True
p = tf.paragraphs[0]
p.text = "$54B"
p.font.size = Pt(36)
p.font.bold = True
p.font.color.rgb = COLOR_AMBER
p2 = tf.add_paragraph()
p2.text = "TAM: EMBEDDED FINTECH"
p2.font.size = Pt(10)
p2.font.bold = True
p2.font.color.rgb = COLOR_WHITE
p3 = tf.add_paragraph()
p3.text = "Global SaaS platforms, marketplaces, and gig-economy platforms adding embedded wallets and multi-currency clearing.\n\nDemands high-concurrency ledger engines with mathematical double-entry guarantees."
p3.font.size = Pt(12)
p3.font.color.rgb = COLOR_SUB
p3.space_before = Pt(14)

add_card(s9, Inches(4.84), y_top, card_w, card_h, COLOR_AMBER)
tb = s9.shapes.add_textbox(Inches(5.09), y_top + Inches(0.3), card_w - Inches(0.5), card_h - Inches(0.6))
tf = tb.text_frame
tf.word_wrap = True
p = tf.paragraphs[0]
p.text = "$12B"
p.font.size = Pt(36)
p.font.bold = True
p.font.color.rgb = COLOR_AMBER
p2 = tf.add_paragraph()
p2.text = "SAM: AGENTIC PAYMENTS"
p2.font.size = Pt(10)
p2.font.bold = True
p2.font.color.rgb = COLOR_WHITE
p3 = tf.add_paragraph()
p3.text = "Autonomous AI agents executing API subscriptions, programmatic bidding, and supplier settlement on behalf of enterprises.\n\nRequires two-phase authorization holds and strict idempotency keys."
p3.font.size = Pt(12)
p3.font.color.rgb = COLOR_SUB
p3.space_before = Pt(14)

add_card(s9, Inches(8.88), y_top, card_w, card_h, COLOR_AMBER)
tb = s9.shapes.add_textbox(Inches(9.13), y_top + Inches(0.3), card_w - Inches(0.5), card_h - Inches(0.6))
tf = tb.text_frame
tf.word_wrap = True
p = tf.paragraphs[0]
p.text = "$850M"
p.font.size = Pt(36)
p.font.bold = True
p.font.color.rgb = COLOR_AMBER
p2 = tf.add_paragraph()
p2.text = "SOM: DEVELOPER CORE WEDGE"
p2.font.size = Pt(10)
p2.font.bold = True
p2.font.color.rgb = COLOR_WHITE
p3 = tf.add_paragraph()
p3.text = "White-label bitemporal wallet engine for gaming micro-economies, creator tipping networks, and peer-to-peer clearing.\n\nZero setup friction, self-contained SQLite WAL backend with instant dockerization."
p3.font.size = Pt(12)
p3.font.color.rgb = COLOR_SUB
p3.space_before = Pt(14)

add_footer(s9)

# -------------------------------------------------------------
# SLIDE 10: CONCLUSION & THE ASK
# -------------------------------------------------------------
s10 = prs.slides.add_slide(blank_layout)
set_slide_background(s10)
add_header(s10, "Summary & Delivery", COLOR_CYAN, "Certified, Pushed & Competition Ready", "Everything required for the WeAreDevelopers × BAND AI Dark Factory Hackathon", 10)

card2_w = Inches(5.6)
# Left: Deliverables Checklist
add_card(s10, Inches(0.8), y_top, card2_w, card_h, COLOR_CYAN)
tb = s10.shapes.add_textbox(Inches(1.05), y_top + Inches(0.3), card2_w - Inches(0.5), card_h - Inches(0.6))
tf = tb.text_frame
tf.word_wrap = True
p = tf.paragraphs[0]
p.text = "Hackathon Compliance Checklist"
p.font.size = Pt(20)
p.font.bold = True
p.font.color.rgb = COLOR_CYAN
p2 = tf.add_paragraph()
p2.text = "ALL CRITERIA 100% SATISFIED"
p2.font.size = Pt(10)
p2.font.bold = True
p2.font.color.rgb = COLOR_WHITE
p3 = tf.add_paragraph()
p3.text = "✔ Public GitHub Repo: Live, public, and clonable\n✔ Official Band Session: Downloaded as room.json\n✔ 3 Generic Mandates: architect.md, implementer.md, reviewer.md\n✔ 4 Stage Folders: stage-1/ through stage-4/ buildable\n✔ 100% Test Pass Rate: 193/193 automated checks passed\n✔ Gate 4 Compliant: 0 vocabulary leaks in mandates\n✔ Gate 3 Compliant: Zero stage overshooting (overshoot: null)"
p3.font.size = Pt(12.5)
p3.font.color.rgb = COLOR_SUB
p3.space_before = Pt(12)

# Right: Repositories & Access
add_card(s10, Inches(6.8), y_top, card2_w, card_h, COLOR_CYAN)
tb = s10.shapes.add_textbox(Inches(7.05), y_top + Inches(0.2), card2_w - Inches(0.5), card_h - Inches(0.4))
tf = tb.text_frame
tf.word_wrap = True
p = tf.paragraphs[0]
p.text = "Repository & Live Access"
p.font.size = Pt(20)
p.font.bold = True
p.font.color.rgb = COLOR_CYAN
p2 = tf.add_paragraph()
p2.text = "SUBMISSION LINKS & DEMO LOGINS"
p2.font.size = Pt(9.5)
p2.font.bold = True
p2.font.color.rgb = COLOR_WHITE
p3 = tf.add_paragraph()
p3.text = "• GitHub Submission Repository:\n  https://github.com/novaecosystems-cloud/Pocketpay\n\n• Live Vercel Deployment: https://temporary-turbo-carbon-owriola.vercel.app\n• Live Demo Login: https://temporary-turbo-carbon-owriola.vercel.app/login\n• Interactive OpenAPI Swagger: https://temporary-turbo-carbon-owriola.vercel.app/docs\n• Team: Nova (@novaecosystems), @architect, @implementer, @reviewer\n\n🔑 Pre-Seeded Demo Logins (Password for all: Password123!):\n  - Alice (@alice): alice@demo.com (€1,500.00 / Primary demo)\n  - Bob (@bob): bob@demo.com (€850.00 / P2P recipient)\n  - Carol (@carol): carol@demo.com (€500.00 / Split requester)\n  *Sidebar click-to-fill active on login screen"
p3.font.size = Pt(11)
p3.font.color.rgb = COLOR_SUB
p3.space_before = Pt(8)

add_footer(s10)

prs.save(out_pptx)
print("SUCCESS: PowerPoint deck generated at:", out_pptx)

# Convert to PDF via PowerPoint COM
import win32com.client
import shutil

out_pdf = os.path.abspath("Pocketpay_Pitch_Deck.pdf")
print("Converting to PDF via PowerPoint COM...")
try:
    ppApp = win32com.client.Dispatch("PowerPoint.Application")
    pres = ppApp.Presentations.Open(os.path.abspath(out_pptx), WithWindow=False)
    pres.SaveAs(out_pdf, 32) # 32 = ppSaveAsPDF
    pres.Close()
    ppApp.Quit()
    print("SUCCESS: PDF deck generated at:", out_pdf)
except Exception as e:
    print("PowerPoint COM PDF export failed:", e)

# Copy both PPTX and PDF to Desktop and Downloads
targets = [
    r"C:\Users\Shourya\Desktop",
    r"D:\Downloads",
    r"C:\Users\Shourya\Downloads"
]

for t in targets:
    if os.path.exists(t):
        try:
            shutil.copy2(out_pptx, os.path.join(t, "Pocketpay_Pitch_Deck.pptx"))
            print(f"Copied PPTX to {t}")
        except Exception as e:
            print(f"Failed to copy PPTX to {t}: {e}")
        if os.path.exists(out_pdf):
            try:
                shutil.copy2(out_pdf, os.path.join(t, "Pocketpay_Pitch_Deck.pdf"))
                print(f"Copied PDF to {t}")
            except Exception as e:
                print(f"Failed to copy PDF to {t}: {e}")
