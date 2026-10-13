const pptxgen = require("pptxgenjs");
const React = require("react");
const RDS = require("react-dom/server");
const sharp = require("sharp");
const fa = require("react-icons/fa");
const path = require("path");
const { applyTheme } = require("C:/Users/User/AppData/Roaming/Claude/local-agent-mode-sessions/skills-plugin/ffee8d08-76d9-40c3-b05e-999ab836268b/f8d8adf1-c9bd-4d4e-a888-1b695217460a/skills/pptx/scripts/apply_theme.js");

const OUT = path.resolve(__dirname, "..", "docs", "Finday_Client_Presentation.pptx");

const THEME = { name: "Finday", headFontFace: "Cambria", bodyFontFace: "Calibri",
  colors: { dk1: "13242B", lt1: "FFFFFF", dk2: "0B3B3C", lt2: "EAF4F2", accent1: "1FA89A", accent2: "F2A541",
    accent3: "5E7C82", accent4: "2F6F73", accent5: "D9644A", accent6: "8FD3C8", hlink: "1FA89A", folHlink: "5E7C82" } };
const H = { teal: "1FA89A", amber: "F2A541", ink: "13242B", deep: "0B3B3C", mist: "EAF4F2", mint: "8FD3C8", slate: "5E7C82", white: "FFFFFF", panel: "114E50" };

async function icon(name, color) {
  const svg = RDS.renderToStaticMarkup(React.createElement(fa[name], { color: "#" + color, size: 256 }));
  const buf = await sharp(Buffer.from(svg)).png().toBuffer();
  return "image/png;base64," + buf.toString("base64");
}

(async () => {
  const pres = new pptxgen();
  pres.layout = "LAYOUT_16x9";
  pres.title = "Finday Intelligence: Client Presentation";
  pres.theme = { headFontFace: "Cambria", bodyFontFace: "Calibri" };
  const C = pres.SchemeColor;

  pres.defineSlideMaster({ title: "DARK", background: { color: H.deep }, objects: [],
    slideNumber: { x: 9.1, y: 5.2, w: 0.5, h: 0.3, color: H.mint, fontSize: 10 } });
  pres.defineSlideMaster({ title: "LIGHT", background: { color: H.white },
    objects: [{ placeholder: { options: { name: "title", type: "title", x: 0.6, y: 0.35, w: 8.8, h: 0.9, fontFace: "Cambria", fontSize: 32, bold: true, color: C.text2, valign: "middle", align: "left", margin: 0 }, text: "" } }],
    slideNumber: { x: 9.1, y: 5.2, w: 0.5, h: 0.3, color: H.slate, fontSize: 10 } });

  const sections = {};
  const addSec = (sec) => { if (!sections[sec]) { pres.addSection({ title: sec }); sections[sec] = 1; } };
  const light = (title, sec) => {
    addSec(sec);
    const s = pres.addSlide({ masterName: "LIGHT", sectionTitle: sec });
    s.addText(title, { placeholder: "title" });
    return s;
  };
  const dark = (sec) => { addSec(sec); return pres.addSlide({ masterName: "DARK", sectionTitle: sec }); };
  const T = (s, text, o) => s.addText(text, Object.assign({ isTextBox: true, margin: 0, color: C.text1 }, o));
  const circle = async (s, ic, x, y, d, bg, fg, name) => {
    s.addShape(pres.ShapeType.ellipse, { x, y, w: d, h: d, fill: { color: bg }, line: { color: bg }, objectName: name + " badge" });
    const k = d * 0.5;
    s.addImage({ data: await icon(ic, fg), x: x + (d - k) / 2, y: y + (d - k) / 2, w: k, h: k, altText: name });
  };
  const card = (s, x, y, w, h, fill = H.mist) =>
    s.addShape(pres.ShapeType.roundRect, { x, y, w, h, rectRadius: 0.12, fill: { color: fill }, line: { color: fill } });

  // 1. Title
  let s = dark("Introduction");
  T(s, "FINDAY INTELLIGENCE", { x: 0.7, y: 1.2, w: 6, h: 0.4, fontSize: 14, bold: true, color: H.amber, charSpacing: 6 });
  T(s, "Know exactly how your business is doing, every day", { x: 0.7, y: 1.7, w: 6.4, h: 1.9, fontSize: 38, bold: true, fontFace: "Cambria", color: C.background1, valign: "top" });
  T(s, "The problems growing businesses face, and how Finday solves them.", { x: 0.7, y: 3.8, w: 5.8, h: 0.8, fontSize: 16, color: H.mint, valign: "top" });
  [[7.4, 0.9, 3.2, "2F6F73"], [7.9, 1.4, 2.2, H.teal], [8.4, 1.9, 1.2, H.amber]].forEach(([x, y, d, c], i) =>
    s.addShape(pres.ShapeType.ellipse, { x, y, w: d, h: d, fill: { color: c }, line: { color: c }, objectName: "pulse ring " + i }));
  s.addImage({ data: await icon("FaChartLine", H.deep), x: 8.7, y: 2.2, w: 0.6, h: 0.6, altText: "Chart icon" });
  s.addNotes("Open with the outcome: daily clarity on profit and cash, without needing an accounting background.");

  // 2. The problem
  s = light("Owners run blind between month-ends", "The problem");
  T(s, "Most owners see their real numbers only when the accountant closes the month, too late to act on them.", { x: 0.6, y: 1.5, w: 3.6, h: 2.2, fontSize: 18, color: C.text2, valign: "top" });
  T(s, "Finday gives you the picture while there is still time to act.", { x: 0.6, y: 3.9, w: 3.6, h: 0.9, fontSize: 14, color: C.accent3, valign: "top" });
  const pains = [["FaTable", "Records in many places", "Sales in a notebook, purchases in bills, expenses in chats."],
    ["FaHourglassHalf", "Numbers that arrive late", "Profit is known only after the month is closed."],
    ["FaFileInvoiceDollar", "Dues kept in memory", "Who owes you, and whom you owe, is not written down anywhere."]];
  for (let i = 0; i < 3; i++) {
    const y = 1.5 + i * 1.15; card(s, 4.6, y, 4.8, 0.98);
    await circle(s, pains[i][0], 4.8, y + 0.19, 0.6, H.deep, H.mint, pains[i][1]);
    T(s, pains[i][1], { x: 5.6, y: y + 0.12, w: 3.7, h: 0.35, fontSize: 16, bold: true, color: C.text2 });
    T(s, pains[i][2], { x: 5.6, y: y + 0.47, w: 3.7, h: 0.45, fontSize: 14, valign: "top" });
  }
  s.addNotes("Ask the room: how do you know this month's profit today? Let them answer before the next slide.");

  // 3. Six issues
  s = light("Six issues hold growing businesses back", "The problem");
  const issues = [["FaTable", "Scattered records", "Entries spread across notebooks, bills and chats."],
    ["FaHourglassHalf", "Late numbers", "Profit is known only after month-end closing."],
    ["FaChartLine", "Hidden margins", "Cost creep and price leaks show up weeks late."],
    ["FaFileInvoiceDollar", "Untracked dues", "Receivables and payables kept in diaries or memory."],
    ["FaHandHoldingUsd", "Manual commissions", "Referral and partner payouts tracked on paper."],
    ["FaUsers", "All-or-nothing access", "Shared logins give staff far more than they need."]];
  for (let i = 0; i < 6; i++) {
    const col = i % 2, row = Math.floor(i / 2), x = 0.6 + col * 4.5, y = 1.45 + row * 1.2;
    card(s, x, y, 4.3, 1.08);
    await circle(s, issues[i][0], x + 0.25, y + 0.24, 0.6, H.teal, H.white, issues[i][1]);
    T(s, issues[i][1], { x: x + 1.1, y: y + 0.14, w: 3.0, h: 0.3, fontSize: 16, bold: true, color: C.text2 });
    T(s, issues[i][2], { x: x + 1.1, y: y + 0.46, w: 3.0, h: 0.52, fontSize: 14, valign: "top" });
  }

  // 4. What we solve
  s = light("What Finday changes, issue by issue", "The solution");
  const fixes = [["Scattered records", "One daily entry screen for sales, purchases and expenses"],
    ["Late numbers", "Dashboard and P&L update from the same-day entries"],
    ["Hidden margins", "Margin and cost trends by category, month and year"],
    ["Untracked dues", "Customer and vendor ledgers with receivables, payables and aging"],
    ["Manual commissions", "Referral commissions tracked per referrer, due and paid"],
    ["All-or-nothing access", "Owner, admin, manager and member logins; managers limited to chosen pages"]];
  for (let i = 0; i < 6; i++) {
    const y = 1.4 + i * 0.63;
    card(s, 0.6, y, 3.1, 0.52);
    T(s, fixes[i][0], { x: 0.8, y, w: 2.8, h: 0.52, fontSize: 14, bold: true, color: C.text2, valign: "middle" });
    await circle(s, "FaArrowRight", 3.85, y + 0.07, 0.38, H.teal, H.white, "arrow " + (i + 1));
    s.addShape(pres.ShapeType.roundRect, { x: 4.4, y, w: 5.0, h: 0.52, rectRadius: 0.12, fill: { color: H.deep }, line: { color: H.deep } });
    T(s, fixes[i][1], { x: 4.6, y, w: 4.7, h: 0.52, fontSize: 14, color: C.background1, valign: "middle" });
  }

  // 5. Three steps
  s = light("Three steps from entry to decision", "The solution");
  const steps = [["1", "Log the day", "Enter sales, purchases and expenses one by one, in bulk, or by importing a spreadsheet."],
    ["2", "Finday organizes", "Ledgers, dues, margins and trends update automatically from those entries."],
    ["3", "You decide", "See what drives growth and what is draining profit, while it can still be fixed."]];
  steps.forEach((st, i) => {
    const x = 0.6 + i * 3.05, col = i == 2 ? H.amber : H.teal;
    s.addShape(pres.ShapeType.ellipse, { x, y: 1.6, w: 0.8, h: 0.8, fill: { color: col }, line: { color: col }, objectName: "step " + st[0] });
    T(s, st[0], { x, y: 1.6, w: 0.8, h: 0.8, fontSize: 26, bold: true, color: i == 2 ? C.text2 : C.background1, align: "center", valign: "middle", fontFace: "Cambria" });
    if (i < 2) s.addShape(pres.ShapeType.line, { x: x + 0.95, y: 2.0, w: 1.95, h: 0, line: { color: H.mint, width: 2, dashType: "dash" } });
    T(s, st[1], { x, y: 2.7, w: 2.7, h: 0.5, fontSize: 22, bold: true, color: C.text2, fontFace: "Cambria" });
    T(s, st[2], { x, y: 3.25, w: 2.7, h: 1.4, fontSize: 15, valign: "top" });
  });

  // 6. Features
  s = light("Features built around those fixes", "The solution");
  const feats = [["FaBolt", "Daily entry and import", "Quick add, bulk grid, spreadsheet import and a printable daily report."],
    ["FaBook", "Ledgers and dues", "Customer, vendor, bank and partner ledgers, with receivables and payables aging."],
    ["FaFileAlt", "Accountant reports", "P&L, Balance Sheet, Cash Flow and GST summary, exported as PDF, Excel or CSV."],
    ["FaHandHoldingUsd", "Commissions and access", "Referral commissions per referrer, plus owner, manager and member logins."]];
  for (let i = 0; i < 4; i++) {
    const col = i % 2, row = Math.floor(i / 2), x = 0.6 + col * 4.5, y = 1.5 + row * 1.85;
    card(s, x, y, 4.3, 1.65);
    await circle(s, feats[i][0], x + 0.25, y + 0.3, 0.7, H.deep, H.mint, feats[i][1]);
    T(s, feats[i][1], { x: x + 1.2, y: y + 0.25, w: 2.9, h: 0.4, fontSize: 16, bold: true, color: C.text2 });
    T(s, feats[i][2], { x: x + 1.2, y: y + 0.7, w: 2.9, h: 0.85, fontSize: 14, valign: "top" });
  }

  // 7. Data choice (dark)
  s = dark("Trust");
  T(s, "Your data, your choice", { x: 0.7, y: 0.4, w: 8, h: 0.8, fontSize: 36, bold: true, fontFace: "Cambria", color: C.background1 });
  const opts = [["FaGoogleDrive", "Your own Google Drive", ["Stored in a Google Sheet in your Drive", "Finday does not keep a copy of it", "Open, export or delete the Sheet yourself", "Revoke Finday's access at any time"]],
    ["FaDatabase", "Finday managed database", ["No Google account needed", "Managed, secure storage for you", "Records kept separate for each organization", "Switch storage later through an approved request"]]];
  for (let i = 0; i < 2; i++) {
    const x = 0.7 + i * 4.4;
    s.addShape(pres.ShapeType.roundRect, { x, y: 1.5, w: 4.1, h: 3.4, rectRadius: 0.12, fill: { color: H.panel }, line: { color: H.panel } });
    await circle(s, opts[i][0], x + 0.3, 1.75, 0.7, H.amber, H.deep, opts[i][1]);
    T(s, opts[i][1], { x: x + 1.15, y: 1.75, w: 2.8, h: 0.7, fontSize: 18, bold: true, color: C.background1, valign: "middle" });
    T(s, opts[i][2].map((t, j) => ({ text: t, options: { bullet: true, breakLine: j < 3 } })), { x: x + 0.3, y: 2.7, w: 3.6, h: 2.0, fontSize: 14, color: C.background2, valign: "top", paraSpaceAfter: 8 });
  }

  // 8. Comparison table
  s = light("Why not stay with spreadsheets?", "Comparison");
  const hdr = (t) => ({ text: t, options: { bold: true, color: H.white, fill: { color: H.deep }, align: "center", valign: "middle" } });
  const cell = (t, o = {}) => ({ text: t, options: Object.assign({ align: "center", valign: "middle" }, o) });
  const rows = [
    [{ text: "What you need", options: { bold: true, color: H.white, fill: { color: H.deep }, valign: "middle" } }, hdr("Spreadsheets"), hdr("Billing apps"), hdr("Accounting software"), hdr("Finday")],
    [cell("Quick daily entry", { align: "left" }), cell("Manual"), cell("Partial"), cell("Heavier"), cell("Yes", { bold: true, color: H.deep })],
    [cell("Live profit dashboard", { align: "left" }), cell("Manual"), cell("Limited"), cell("Limited"), cell("Yes", { bold: true, color: H.deep })],
    [cell("P&L, Balance Sheet, Cash Flow", { align: "left" }), cell("Manual"), cell("Limited"), cell("Yes"), cell("Yes", { bold: true, color: H.deep })],
    [cell("Dues and aging", { align: "left" }), cell("Manual"), cell("Yes"), cell("Yes"), cell("Yes", { bold: true, color: H.deep })],
    [cell("Referral commissions", { align: "left" }), cell("Manual"), cell("No"), cell("No"), cell("Yes", { bold: true, color: H.deep })],
    [cell("Invoicing", { align: "left" }), cell("Manual"), cell("Yes"), cell("Yes"), cell("Not included")],
    [cell("Data in your own Drive", { align: "left" }), cell("Yes"), cell("No"), cell("Varies"), cell("Yes", { bold: true, color: H.deep })],
  ];
  s.addTable(rows, { x: 0.6, y: 1.4, w: 8.8, colW: [2.8, 1.5, 1.5, 1.6, 1.4], rowH: 0.42, fontFace: "Calibri", fontSize: 14, color: C.text1, border: { type: "solid", pt: 0.5, color: "CBD9D6" } });
  T(s, "Comparison based on public information from October 2026. Verify competitor details before quoting.", { x: 0.6, y: 4.9, w: 8.8, h: 0.3, fontSize: 11, color: C.accent3, italic: true });

  // 9. Who it's for
  s = light("Built for businesses like yours", "Who it is for");
  const inds = [["FaStore", "Retail"], ["FaUtensils", "Restaurants"], ["FaHospital", "Hospitals and clinics"], ["FaTruck", "Wholesale"], ["FaShoppingCart", "E-commerce"], ["FaHandshake", "Services"], ["FaIndustry", "Manufacturing"], ["FaPlus", "And more"]];
  for (let i = 0; i < 8; i++) {
    const col = i % 4, row = Math.floor(i / 4), x = 0.6 + col * 2.25, y = 1.6 + row * 1.7;
    card(s, x, y, 2.05, 1.5);
    await circle(s, inds[i][0], x + 0.75, y + 0.2, 0.55, H.teal, H.white, inds[i][1]);
    T(s, inds[i][1], { x: x + 0.1, y: y + 0.85, w: 1.85, h: 0.5, fontSize: 14, bold: true, align: "center", color: C.text2, valign: "top" });
  }

  // 10. Plans
  s = light("Plans that grow with your business", "Pricing");
  const plans = [["Professional", "1,999", "23,988", "1 login", "Finday managed database"],
    ["Professional Drive", "2,499", "29,988", "1 login", "Your Google Drive"],
    ["Business", "2,499", "29,988", "2 logins", "Your Google Drive"],
    ["Business Drive", "2,999", "35,988", "2 logins", "Your Google Drive"]];
  for (let i = 0; i < 4; i++) {
    const x = 0.5 + i * 2.35;
    card(s, x, 1.45, 2.15, 3.55, i == 2 ? H.deep : H.mist);
    const tc = i == 2 ? C.background1 : C.text2, sc = i == 2 ? H.mint : C.accent3;
    T(s, plans[i][0], { x: x + 0.2, y: 1.6, w: 1.8, h: 0.6, fontSize: 16, bold: true, color: tc, valign: "top" });
    T(s, "INR " + plans[i][1], { x: x + 0.2, y: 2.2, w: 1.8, h: 0.5, fontSize: 24, bold: true, fontFace: "Cambria", color: i == 2 ? H.amber : C.accent4 });
    T(s, "per month", { x: x + 0.2, y: 2.7, w: 1.8, h: 0.3, fontSize: 14, color: sc });
    T(s, "Yearly: " + plans[i][2], { x: x + 0.2, y: 3.05, w: 1.8, h: 0.3, fontSize: 14, color: sc });
    T(s, plans[i][3], { x: x + 0.2, y: 3.5, w: 1.8, h: 0.3, fontSize: 14, bold: true, color: tc });
    T(s, plans[i][4], { x: x + 0.2, y: 3.85, w: 1.8, h: 0.9, fontSize: 14, color: sc, valign: "top" });
  }
  T(s, "Indicative prices in INR, per plan. Every plan starts with a 14-day trial. Confirm the final price and GST before signing.", { x: 0.5, y: 5.08, w: 8.6, h: 0.3, fontSize: 11, color: C.accent3, italic: true });
  s.addNotes("Prices are indicative. Confirm the final figures, GST treatment and the storage option for each plan before the meeting.");

  // 11. Security
  s = light("Security and trust, built in", "Trust");
  const sec = [["FaLock", "Your own workspace", "Each organization's data is kept separate from every other organization."],
    ["FaUserShield", "Protected sign-in", "Strong password rules, plus a one-time code by email for password recovery."],
    ["FaGoogleDrive", "Limited Google access", "Finday asks only for the file it creates, and tokens are stored encrypted."],
    ["FaFilePdf", "Always portable", "Download your reports as PDF, or your data as Excel or CSV, at any time."]];
  for (let i = 0; i < 4; i++) {
    const col = i % 2, row = Math.floor(i / 2), x = 0.6 + col * 4.5, y = 1.5 + row * 1.85;
    card(s, x, y, 4.3, 1.65);
    await circle(s, sec[i][0], x + 0.25, y + 0.3, 0.7, H.teal, H.white, sec[i][1]);
    T(s, sec[i][1], { x: x + 1.2, y: y + 0.25, w: 2.9, h: 0.4, fontSize: 16, bold: true, color: C.text2 });
    T(s, sec[i][2], { x: x + 1.2, y: y + 0.7, w: 2.9, h: 0.85, fontSize: 14, valign: "top" });
  }

  // 12. Next steps (dark)
  s = dark("Next steps");
  T(s, "See your business in one place", { x: 0.7, y: 1.1, w: 8.2, h: 1.0, fontSize: 36, bold: true, fontFace: "Cambria", color: C.background1, valign: "top" });
  const nxt = ["Book a live demo with your own sample data", "Start a 14-day trial and connect your Google Drive", "Enter your first week of entries together with us"];
  nxt.forEach((t, i) => {
    const y = 2.4 + i * 0.85;
    s.addShape(pres.ShapeType.ellipse, { x: 0.7, y, w: 0.55, h: 0.55, fill: { color: H.amber }, line: { color: H.amber }, objectName: "next " + (i + 1) });
    T(s, String(i + 1), { x: 0.7, y, w: 0.55, h: 0.55, fontSize: 18, bold: true, color: C.text2, align: "center", valign: "middle", fontFace: "Cambria" });
    T(s, t, { x: 1.5, y, w: 7.2, h: 0.55, fontSize: 18, color: C.background2, valign: "middle" });
  });
  s.addNotes("Close by agreeing the first concrete step, the demo date and who will connect the Google Drive.");

  await pres.writeFile({ fileName: OUT });
  await applyTheme(OUT, THEME);
  console.log("written", OUT);
})().catch(e => { console.error(e); process.exit(1); });
