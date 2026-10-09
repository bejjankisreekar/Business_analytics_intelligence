const pptxgen = require("pptxgenjs");
const React = require("react");
const RDS = require("react-dom/server");
const sharp = require("sharp");
const fa = require("react-icons/fa");
const { applyTheme } = require("C:/Users/User/AppData/Roaming/Claude/local-agent-mode-sessions/skills-plugin/ffee8d08-76d9-40c3-b05e-999ab836268b/f8d8adf1-c9bd-4d4e-a888-1b695217460a/skills/pptx/scripts/apply_theme.js");

const THEME = { name: "Finday", headFontFace: "Cambria", bodyFontFace: "Calibri",
  colors: { dk1: "13242B", lt1: "FFFFFF", dk2: "0B3B3C", lt2: "EAF4F2", accent1: "1FA89A", accent2: "F2A541",
    accent3: "5E7C82", accent4: "2F6F73", accent5: "D9644A", accent6: "8FD3C8", hlink: "1FA89A", folHlink: "5E7C82" } };
const H = { teal: "1FA89A", amber: "F2A541", ink: "13242B", deep: "0B3B3C", mist: "EAF4F2", mint: "8FD3C8", slate: "5E7C82", white: "FFFFFF" };

async function icon(name, color) {
  const svg = RDS.renderToStaticMarkup(React.createElement(fa[name], { color: "#" + color, size: 256 }));
  const buf = await sharp(Buffer.from(svg)).png().toBuffer();
  return "image/png;base64," + buf.toString("base64");
}

(async () => {
  const pres = new pptxgen();
  pres.layout = "LAYOUT_16x9";
  pres.title = "Finday Intelligence";
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

  // 1 Title
  let s = dark("Intro");
  T(s, "FINDAY", { x: 0.7, y: 1.2, w: 6, h: 0.4, fontSize: 14, bold: true, color: H.amber, charSpacing: 6 });
  T(s, "Know exactly how your business is doing, every single day", { x: 0.7, y: 1.7, w: 6.4, h: 1.9, fontSize: 38, bold: true, fontFace: "Cambria", color: C.background1, valign: "top" });
  T(s, "Intelligence for owners who run the business, not just watch it.", { x: 0.7, y: 3.8, w: 5.8, h: 0.8, fontSize: 16, color: H.mint, valign: "top" });
  [[7.4, 0.9, 3.2, "2F6F73"], [7.9, 1.4, 2.2, H.teal], [8.4, 1.9, 1.2, H.amber]].forEach(([x, y, d, c], i) =>
    s.addShape(pres.ShapeType.ellipse, { x, y, w: d, h: d, fill: { color: c }, line: { color: c }, objectName: "pulse ring " + i }));
  s.addImage({ data: await icon("FaChartLine", H.deep), x: 8.7, y: 2.2, w: 0.6, h: 0.6, altText: "Chart icon" });
  s.addNotes("Open with the promise: clarity every day, without needing an accounting background.");

  // 2 Problem
  s = light("Most owners find out too late", "Why");
  const pains = [["FaTable", "Scattered spreadsheets", "Sales in one file, expenses in another, nothing ties together."],
    ["FaHourglassHalf", "End-of-month surprises", "You learn about a margin leak weeks after it started."],
    ["FaFileInvoiceDollar", "Unpaid bills & dues", "Who owes you, and whom you owe, lives in someone's head."]];
  for (let i = 0; i < 3; i++) {
    const x = 0.6 + i * 3.0; card(s, x, 1.6, 2.8, 3.1);
    await circle(s, pains[i][0], x + 0.3, 1.9, 0.7, H.deep, H.mint, pains[i][1]);
    T(s, pains[i][1], { x: x + 0.3, y: 2.8, w: 2.3, h: 0.7, fontSize: 18, bold: true, color: C.text2, valign: "top" });
    T(s, pains[i][2], { x: x + 0.3, y: 3.5, w: 2.3, h: 1.0, fontSize: 14, valign: "top" });
  }

  // 3 How it works
  s = light("Three steps, a couple of minutes a day", "Why");
  const steps = [["1", "Log it", "Enter today's sales, purchases and expenses: one by one, in bulk, or imported from a spreadsheet."],
    ["2", "We crunch it", "Margins, trends, ledgers and comparisons are produced automatically."],
    ["3", "You decide", "See what's driving growth and what's quietly draining profit, before it becomes a problem."]];
  steps.forEach((st, i) => {
    const x = 0.6 + i * 3.05, col = i == 2 ? H.amber : H.teal;
    s.addShape(pres.ShapeType.ellipse, { x, y: 1.6, w: 0.8, h: 0.8, fill: { color: col }, line: { color: col }, objectName: "step " + st[0] });
    T(s, st[0], { x, y: 1.6, w: 0.8, h: 0.8, fontSize: 26, bold: true, color: i == 2 ? C.text2 : C.background1, align: "center", valign: "middle", fontFace: "Cambria" });
    if (i < 2) s.addShape(pres.ShapeType.line, { x: x + 0.95, y: 2.0, w: 1.95, h: 0, line: { color: H.mint, width: 2, dashType: "dash" } });
    T(s, st[1], { x, y: 2.7, w: 2.7, h: 0.5, fontSize: 22, bold: true, color: C.text2, fontFace: "Cambria" });
    T(s, st[2], { x, y: 3.25, w: 2.7, h: 1.4, fontSize: 15, valign: "top" });
  });

  // 4 Who it's for
  s = light("Made for any business with daily sales", "Why");
  const inds = [["FaStore", "Retail"], ["FaUtensils", "Restaurants"], ["FaShoppingCart", "E-commerce"], ["FaHandshake", "Services"], ["FaTruck", "Wholesale"], ["FaIndustry", "Manufacturing"], ["FaHospital", "Hospitals"], ["FaPlus", "...and more"]];
  for (let i = 0; i < 8; i++) {
    const col = i % 4, row = Math.floor(i / 4), x = 0.6 + col * 2.25, y = 1.6 + row * 1.7;
    card(s, x, y, 2.05, 1.5);
    await circle(s, inds[i][0], x + 0.75, y + 0.2, 0.55, H.teal, H.white, inds[i][1]);
    T(s, inds[i][1], { x: x + 0.1, y: y + 0.85, w: 1.85, h: 0.5, fontSize: 14, bold: true, align: "center", color: C.text2 });
  }

  const feat = async (title, lead, items, ic) => {
    const sl = light(title, "Features");
    T(sl, lead, { x: 0.6, y: 1.4, w: 3.5, h: 1.7, fontSize: 16, valign: "top" });
    await circle(sl, ic, 0.6, 3.4, 1.1, H.deep, H.mint, title);
    items.forEach((it, i) => {
      const y = 1.4 + i * 0.95; card(sl, 4.4, y, 5.0, 0.8);
      sl.addShape(pres.ShapeType.ellipse, { x: 4.6, y: y + 0.27, w: 0.26, h: 0.26, fill: { color: H.teal }, line: { color: H.teal } });
      T(sl, [{ text: it[0] + "  ", options: { bold: true, color: C.text2 } }, { text: it[1], options: {} }], { x: 5.05, y, w: 4.2, h: 0.8, fontSize: 14, valign: "middle" });
    });
  };

  await feat("Fast daily entry & imports", "Capture the day in seconds. No accounting background needed, just enter what happened.",
    [["Quick add:", "sales, expenses and purchases from the dashboard."], ["Bulk entry:", "a whole day's rows in one grid."], ["Spreadsheet import:", "upload sales, review, then commit."], ["Daily report:", "a clean snapshot of any day, as PDF."]], "FaBolt");
  await feat("Ledgers for every relationship", "Running-balance ledgers keep every customer, vendor, bank account and partner always current.",
    [["Customer ledgers:", "what each customer bought and paid."], ["Vendor ledgers:", "balance owed to every supplier."], ["Bank accounts:", "balances and transfers between accounts."], ["Partner ledgers:", "capital and withdrawals by partner."]], "FaBook");
  await feat("Receivables, payables & cash", "Know what's owed to you, what you owe and what cash you actually have, live.",
    [["Receivables:", "record payments against customer dues."], ["Payables:", "track vendor bills and settle them."], ["Aging report:", "dues bucketed by days overdue."], ["Cash position:", "cash in hand and upcoming obligations."]], "FaWallet");
  await feat("Sales & purchase intelligence", "Auto-generated insights show what's gaining momentum and what's losing it.",
    [["Revenue intelligence:", "which channel, product or department drives income."], ["Cost intelligence:", "every cost category compared month to month."], ["Patterns:", "best-selling days and payment-mode mix."], ["Early warnings:", "catch creeping expenses sooner."]], "FaChartPie");
  await feat("Reports your accountant will ask for", "Monthly summaries, category statements and full reports for any period you choose.",
    [["Monthly & yearly summaries:", "revenue, bills, average bill."], ["Category statements:", "drill into any category."], ["Financial statements:", "P&L, Balance Sheet, Cash Flow."], ["Export anywhere:", "PDF, Excel and CSV."]], "FaFileAlt");

  // Demo chart
  s = light("See it in action: six months at a glance", "Proof");
  s.addChart(pres.charts.BAR, [{ name: "Revenue (lakh)", labels: ["Apr", "May", "Jun", "Jul", "Aug", "Sep"], values: [39.5, 39.3, 32.8, 30.6, 34.8, 31.8] }],
    { x: 0.6, y: 1.4, w: 5.6, h: 3.6, chartColors: [H.teal], showTitle: true, title: "Monthly revenue, ₹ lakh", titleFontSize: 14, titleColor: H.deep, titleFontFace: "+mn-lt",
      showValue: true, dataLabelPosition: "outEnd", dataLabelFontSize: 12, dataLabelFormatCode: "0.0", dataLabelColor: H.ink, dataLabelFontFace: "+mn-lt",
      catAxisLabelColor: H.slate, valAxisLabelColor: H.slate, catAxisLabelFontFace: "+mn-lt", valAxisLabelFontFace: "+mn-lt", catAxisLabelFontSize: 12, valAxisLabelFontSize: 12,
      valGridLine: { color: "D5E3E1", size: 0.5 }, catGridLine: { style: "none" }, showLegend: false, valAxisMinVal: 0 });
  [["₹31.8L", "September revenue"], ["68.4%", "net margin"], ["183", "bills in the month"]].forEach((k, i) => {
    const y = 1.4 + i * 1.25; card(s, 6.6, y, 2.8, 1.1);
    T(s, k[0], { x: 6.85, y: y + 0.1, w: 2.4, h: 0.6, fontSize: 30, bold: true, fontFace: "Cambria", color: C.accent4 });
    T(s, k[1], { x: 6.85, y: y + 0.7, w: 2.4, h: 0.3, fontSize: 12, color: C.accent3 });
  });
  T(s, "Demo data: a multi-department hospital using the app for daily revenue and costs.", { x: 0.6, y: 5.1, w: 8, h: 0.25, fontSize: 10, color: C.accent3, italic: true });

  // Data choice (dark)
  s = dark("Trust");
  T(s, "Your data, your choice", { x: 0.7, y: 0.4, w: 8, h: 0.8, fontSize: 36, bold: true, fontFace: "Cambria", color: C.background1 });
  const opts = [["FaGoogleDrive", "Your own Google Drive", ["Data lives in a Google Sheet in your Drive", "You own the file: open or export any time", "We never keep a copy of your financial data", "We touch only the one Sheet we create"]],
    ["FaDatabase", "Finday managed database", ["No Google account needed", "Secure and managed for you", "Dedicated, isolated data store", "Switch later with a storage change request"]]];
  for (let i = 0; i < 2; i++) {
    const x = 0.7 + i * 4.4; s.addShape(pres.ShapeType.roundRect, { x, y: 1.5, w: 4.1, h: 3.4, rectRadius: 0.12, fill: { color: "114E50" }, line: { color: "114E50" } });
    await circle(s, opts[i][0], x + 0.3, 1.75, 0.7, H.amber, H.deep, opts[i][1]);
    T(s, opts[i][1], { x: x + 1.15, y: 1.75, w: 2.8, h: 0.7, fontSize: 18, bold: true, color: C.background1, valign: "middle" });
    T(s, opts[i][2].map((t, j) => ({ text: t, options: { bullet: true, breakLine: j < 3 } })), { x: x + 0.3, y: 2.7, w: 3.6, h: 2.0, fontSize: 14, color: C.background2, valign: "top", paraSpaceAfter: 8 });
  }

  // Security
  s = light("Private by design, simple for a team", "Trust");
  const sec = [["FaLock", "Isolated workspace", "Every organization gets its own data store, never shared or mixed."],
    ["FaUserFriends", "Team access", "Owner plus manager logins, sized to your plan."],
    ["FaUserShield", "Secure sign-in", "Protected accounts and encrypted connections."],
    ["FaFilePdf", "Always portable", "Export your reports and data whenever you need them."]];
  for (let i = 0; i < 4; i++) {
    const col = i % 2, row = Math.floor(i / 2), x = 0.6 + col * 4.5, y = 1.5 + row * 1.7;
    card(s, x, y, 4.3, 1.5);
    await circle(s, sec[i][0], x + 0.25, y + 0.4, 0.7, H.teal, H.white, sec[i][1]);
    T(s, sec[i][1], { x: x + 1.2, y: y + 0.2, w: 2.9, h: 0.4, fontSize: 16, bold: true, color: C.text2 });
    T(s, sec[i][2], { x: x + 1.2, y: y + 0.62, w: 2.9, h: 0.8, fontSize: 13, valign: "top" });
  }

  // Plans
  s = light("Simple plans, transparent billing", "Trust");
  const pl = [["FaGift", "Start free", "Try it with your own data. No credit card required."],
    ["FaLayerGroup", "Pick your plan", "Plans by storage choice and team size, monthly or yearly."],
    ["FaCreditCard", "Pay your way", "Card, UPI and netbanking via Razorpay, with optional autopay."],
    ["FaReceipt", "Clear invoices", "Invoices, payment history and coupons on one billing page."]];
  for (let i = 0; i < 4; i++) {
    const x = 0.6 + i * 2.25; card(s, x, 1.6, 2.05, 3.2);
    await circle(s, pl[i][0], x + 0.7, 1.85, 0.65, i == 0 ? H.amber : H.teal, i == 0 ? H.deep : H.white, pl[i][1]);
    T(s, pl[i][1], { x: x + 0.15, y: 2.7, w: 1.75, h: 0.5, fontSize: 16, bold: true, align: "center", color: C.text2 });
    T(s, pl[i][2], { x: x + 0.15, y: 3.25, w: 1.75, h: 1.4, fontSize: 13, align: "center", valign: "top" });
  }

  // CTA
  s = dark("Close");
  T(s, "See what your numbers have been trying to tell you", { x: 0.7, y: 1.2, w: 6.6, h: 1.8, fontSize: 34, bold: true, fontFace: "Cambria", color: C.background1, valign: "top" });
  T(s, "Create your organization, free. Your own data, set up in minutes.", { x: 0.7, y: 3.2, w: 6, h: 0.7, fontSize: 18, color: H.mint, valign: "top" });
  s.addShape(pres.ShapeType.roundRect, { x: 0.7, y: 4.1, w: 2.8, h: 0.6, rectRadius: 0.3, fill: { color: H.amber }, line: { color: H.amber } });
  T(s, "Start free today", { x: 0.7, y: 4.1, w: 2.8, h: 0.6, fontSize: 16, bold: true, color: C.text2, align: "center", valign: "middle" });
  [[7.5, 1.2, 2.2, "2F6F73"], [8.0, 1.7, 1.2, H.teal]].forEach(([x, y, d, c], i) => s.addShape(pres.ShapeType.ellipse, { x, y, w: d, h: d, fill: { color: c }, line: { color: c }, objectName: "pulse ring " + i }));
  s.addImage({ data: await icon("FaRocket", H.white), x: 8.25, y: 1.95, w: 0.7, h: 0.7, altText: "Rocket" });

  await pres.writeFile({ fileName: "Finday_Client_Deck.pptx" });
  await applyTheme("Finday_Client_Deck.pptx", THEME);
  console.log("ok");
})().catch(e => { console.error(e); process.exit(1); });
