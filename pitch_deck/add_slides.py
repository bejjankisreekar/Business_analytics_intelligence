"""Adds product-tour, pricing and contact slides to the (hand-edited) deck in place."""
import copy
from pptx import Presentation
from pptx.util import Inches, Pt
from pptx.dml.color import RGBColor
from pptx.enum.dml import MSO_THEME_COLOR as TC
from pptx.enum.shapes import MSO_SHAPE
from pptx.enum.text import PP_ALIGN, MSO_ANCHOR
from PIL import Image

SRC = "Prism_Pulse_Client_Deck.backup.pptx"
OUT = "Prism_Pulse_Client_Deck_v2.pptx"
prs = Presentation(SRC)
L = {l.name: l for l in prs.slide_layouts}
sldnum = [sh for sh in prs.slides[9].shapes if sh.name.startswith("Slide Number")][0]._element
title_el = [sh for sh in prs.slides[9].shapes if sh.name == "Text 0"][0]._element
sldnum_dark = [sh for sh in prs.slides[13].shapes if sh.name.startswith("Slide Number")][0]._element


def color(fmt, c):
    if isinstance(c, RGBColor):
        fmt.rgb = c
    else:
        fmt.theme_color = c


def new_slide(layout, title=None):
    s = prs.slides.add_slide(L[layout])
    if title is not None:
        el = copy.deepcopy(title_el)
        s.shapes._spTree.append(el)
        ts = [sh for sh in s.shapes if sh._element is el][0]
        ts.text_frame.paragraphs[0].runs[0].text = title
        for extra in ts.text_frame.paragraphs[0].runs[1:]:
            extra.text = ""
    s.shapes._spTree.append(copy.deepcopy(sldnum_dark if layout == "DARK" else sldnum))
    return s


def shape(s, kind, x, y, w, h, fill, radius=None, name=None):
    sh = s.shapes.add_shape(kind, Inches(x), Inches(y), Inches(w), Inches(h))
    sh.fill.solid(); color(sh.fill.fore_color, fill)
    sh.line.fill.background()
    sh.shadow.inherit = False
    if radius is not None and kind == MSO_SHAPE.ROUNDED_RECTANGLE:
        sh.adjustments[0] = radius
    if name:
        sh.name = name
    return sh


def text(s, runs, x, y, w, h, size=14, col=TC.TEXT_1, bold=False, align=PP_ALIGN.LEFT,
         anchor=MSO_ANCHOR.TOP, head=False, italic=False, name=None):
    tb = s.shapes.add_textbox(Inches(x), Inches(y), Inches(w), Inches(h))
    if name:
        tb.name = name
    tf = tb.text_frame
    tf.word_wrap = True
    tf.margin_left = tf.margin_right = tf.margin_top = tf.margin_bottom = 0
    tf.vertical_anchor = anchor
    p = tf.paragraphs[0]; p.alignment = align
    if isinstance(runs, str):
        runs = [(runs, {})]
    for t, o in runs:
        r = p.add_run(); r.text = t
        f = r.font
        f.size = Pt(o.get("size", size)); f.bold = o.get("bold", bold); f.italic = o.get("italic", italic)
        color(f.color, o.get("col", col))
        if o.get("head", head):
            f.name = "+mj-lt"
        if o.get("strike"):
            r._r.get_or_add_rPr().set("strike", "sngStrike")
    return tb


# ---------------- product tour slides ----------------
tour = [
    ("Your dashboard and revenue drivers", "analytics", "revenue"),
    ("Where the money goes, month by month", "cost", "monthly"),
    ("Vendor balances and what's overdue", "vendors", "aging"),
]
tour_slides = []
for title, a, b in tour:
    s = new_slide("LIGHT", title)
    for i, k in enumerate((a, b)):
        path = f"shots/{k}.png"
        w_px, h_px = Image.open(path).size
        w = 4.3
        h = w * h_px / w_px
        pic = s.shapes.add_picture(path, Inches(0.6 + i * 4.5), Inches(1.45), Inches(w), Inches(h))
        pic.name = f"Screenshot {k}"
        pic._element.nvPicPr.cNvPr.set("descr", f"Product screenshot: {k} view")
    text(s, "Real numbers from a live account: a multi-department hospital using Prism Pulse every day.",
         0.6, 4.85, 8.2, 0.25, size=11, col=TC.ACCENT_3, italic=True, name="Screenshot caption")
    tour_slides.append(s)

# ---------------- pricing slide ----------------
s = new_slide("LIGHT", "Pricing: pick where your data lives")
plans = [
    ("Professional", "Prism Pulse DB", "1 team member", "1,299", "1,999", "35%", "15,592", False),
    ("Professional Drive", "Your Google Drive", "1 team member", "1,499", "2,499", "40%", "17,993", False),
    ("Business", "Prism Pulse DB", "2 team members", "1,599", "2,499", "36%", "19,192", True),
    ("Business Drive", "Your Google Drive", "2 team members", "1,799", "2,999", "40%", "21,593", False),
]
cw, gap, y0, ch = 2.05, 0.2, 1.45, 3.1
for i, (name, store, team, mo, mo_list, disc, yr, rec) in enumerate(plans):
    x = 0.6 + i * (cw + gap)
    base = TC.TEXT_2 if rec else TC.BACKGROUND_2
    fg = TC.BACKGROUND_1 if rec else TC.TEXT_2
    body = TC.BACKGROUND_2 if rec else TC.TEXT_1
    shape(s, MSO_SHAPE.ROUNDED_RECTANGLE, x, y0, cw, ch, base, 0.06, f"{name} card")
    text(s, name, x + 0.2, y0 + 0.2, cw - 0.3, 0.35, size=14, col=fg, bold=True, name=f"{name} name")
    pill = shape(s, MSO_SHAPE.ROUNDED_RECTANGLE, x + 0.2, y0 + 0.62, 1.5, 0.28,
                 TC.ACCENT_2 if rec else TC.ACCENT_1, 0.5, f"{name} storage pill")
    text(s, store, x + 0.2, y0 + 0.62, 1.5, 0.28, size=11, col=TC.TEXT_2 if rec else TC.BACKGROUND_1,
         bold=True, align=PP_ALIGN.CENTER, anchor=MSO_ANCHOR.MIDDLE, name=f"{name} storage")
    text(s, [("₹" + mo, {"size": 28, "bold": True, "head": True, "col": fg}),
             (" /mo", {"size": 12, "col": body})], x + 0.2, y0 + 1.05, cw - 0.4, 0.5, anchor=MSO_ANCHOR.MIDDLE,
         name=f"{name} price")
    text(s, [("₹" + mo_list, {"size": 12, "strike": True, "col": body}),
             ("   save " + disc, {"size": 12, "bold": True, "col": TC.ACCENT_2 if rec else TC.ACCENT_4})],
         x + 0.2, y0 + 1.6, cw - 0.4, 0.3, name=f"{name} list price")
    text(s, "or ₹" + yr + " /year", x + 0.2, y0 + 1.95, cw - 0.4, 0.3, size=12, col=body, name=f"{name} yearly")
    text(s, team, x + 0.2, y0 + 2.4, cw - 0.4, 0.3, size=13, bold=True, col=fg, name=f"{name} team")
    text(s, "7-day free trial", x + 0.2, y0 + 2.68, cw - 0.4, 0.3, size=13, col=body, name=f"{name} trial")
    if rec:
        tag = shape(s, MSO_SHAPE.ROUNDED_RECTANGLE, x + cw / 2 - 0.7, y0 - 0.15, 1.4, 0.3, TC.ACCENT_2, 0.5, "Recommended pill")
        text(s, "Recommended", x + cw / 2 - 0.7, y0 - 0.15, 1.4, 0.3, size=11, bold=True, col=TC.TEXT_2,
             align=PP_ALIGN.CENTER, anchor=MSO_ANCHOR.MIDDLE, name="Recommended")
text(s, [("Every plan includes: ", {"bold": True, "col": TC.TEXT_2}),
         ("business dashboard, revenue, expense and purchase analytics, P&L, cash flow, reports, export and AI insights.", {})],
     0.6, 4.7, 8.8, 0.4, size=13, name="Included in every plan")

# ---------------- contact slide ----------------
c = new_slide("DARK")
text(c, "Let's talk", 0.7, 1.0, 6, 0.9, size=40, bold=True, head=True, col=TC.BACKGROUND_1, name="Contact title")
text(c, "Questions before you sign up? Reach out directly.", 0.7, 1.95, 6, 0.5, size=18, col=TC.ACCENT_6,
     name="Contact lead")
rows = [("icons/FaEnvelope.png", "Email", "prismpulse.intelligence@gmail.com"),
        ("icons/FaPhoneAlt.png", "Phone", "9700364800")]
for i, (ic, label, val) in enumerate(rows):
    y = 2.9 + i * 0.95
    shape(c, MSO_SHAPE.OVAL, 0.7, y, 0.7, 0.7, TC.ACCENT_2, name=f"{label} badge")
    pic = c.shapes.add_picture(ic, Inches(0.7 + 0.19), Inches(y + 0.19), Inches(0.32), Inches(0.32))
    pic._element.nvPicPr.cNvPr.set("descr", label + " icon")
    text(c, label.upper(), 1.65, y + 0.02, 4, 0.25, size=11, bold=True, col=TC.ACCENT_6, name=f"{label} label")
    text(c, val, 1.65, y + 0.28, 5.2, 0.4, size=20, bold=True, col=TC.BACKGROUND_1, name=f"{label} value")
for k, (x, y, d, col) in enumerate([(7.5, 1.2, 2.2, TC.ACCENT_4), (8.0, 1.7, 1.2, TC.ACCENT_1)]):
    shape(c, MSO_SHAPE.OVAL, x, y, d, d, col, name=f"pulse ring {k}")
ok = c.shapes.add_picture("icons/FaCheckCircle.png", Inches(8.3), Inches(2.0), Inches(0.6), Inches(0.6))
ok._element.nvPicPr.cNvPr.set("descr", "Check icon")
text(c, "Free to start. No credit card required.", 7.0, 3.6, 2.6, 0.7, size=14, col=TC.ACCENT_6,
     align=PP_ALIGN.CENTER, name="Contact footnote")

# ---------------- reorder ----------------
ids = list(prs.slides._sldIdLst)
orig, tours, pricing, contact = ids[:14], ids[14:17], ids[17], ids[18]
# orig index: slide10 = 9 (chart) -> tours after; slide13 = 12 (plans) -> pricing after; slide14 = 13 CTA -> contact last
order = orig[:10] + tours + orig[10:13] + [pricing] + [orig[13], contact]
lst = prs.slides._sldIdLst
for e in ids:
    lst.remove(e)
for e in order:
    lst.append(e)
prs.save(OUT)
print("saved", len(order), "slides")
