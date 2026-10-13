const pptxgen = require("pptxgenjs");
const React = require("react");
const RDS = require("react-dom/server");
const sharp = require("sharp");
const fa = require("react-icons/fa");
const path = require("path");
const { applyTheme } = require("C:/Users/User/AppData/Roaming/Claude/local-agent-mode-sessions/skills-plugin/ffee8d08-76d9-40c3-b05e-999ab836268b/f8d8adf1-c9bd-4d4e-a888-1b695217460a/skills/pptx/scripts/apply_theme.js");

const OUT = path.resolve(__dirname, "..", "docs", "Finday_Hospital_Presentation.pptx");

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
  pres.title = "Finday Intelligence for Hospitals";
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
  T(s, "FINDAY INTELLIGENCE FOR HOSPITALS", { x: 0.7, y: 1.2, w: 7, h: 0.4, fontSize: 14, bold: true, color: H.amber, charSpacing: 4 });
  T(s, "Know every department's profit, every day", { x: 0.7, y: 1.7, w: 6.4, h: 1.9, fontSize: 38, bold: true, fontFace: "Cambria", color: C.background1, valign: "top" });
  T(s, "Daily financial clarity for hospital owners, administrators and accounts teams.", { x: 0.7, y: 3.8, w: 5.8, h: 0.8, fontSize: 16, color: H.mint, valign: "top" });
  [[7.4, 0.9, 3.2, "2F6F73"], [7.9, 1.4, 2.2, H.teal], [8.4, 1.9, 1.2, H.amber]].forEach(([x, y, d, c], i) =>
    s.addShape(pres.ShapeType.ellipse, { x, y, w: d, h: d, fill: { color: c }, line: { color: c }, objectName: "pulse ring " + i }));
  s.addImage({ data: await icon("FaHospital", H.deep), x: 8.7, y: 2.2, w: 0.6, h: 0.6, altText: "Hospital icon" });
  s.addNotes("Open with the outcome: every department's revenue, costs and dues visible daily, without waiting for month-end.");

  // 2. The problem
  s = light("A hospital is many businesses in one", "The problem");
  T(s, "Revenue comes from OPD, IPD, pharmacy and lab, each with its own costs, dues and doctors.", { x: 0.6, y: 1.5, w: 3.6, h: 2.2, fontSize: 18, color: C.text2, valign: "top" });
  T(s, "Finday brings these numbers together while there is still time to act.", { x: 0.6, y: 3.9, w: 3.6, h: 0.9, fontSize: 14, color: C.accent3, valign: "top" });
  const pains = [["FaHospital", "Departments in silos", "Each department's revenue and costs are tracked separately, if at all."],
    ["FaUserMd", "Doctor commissions by hand", "Referral and consulting payouts are kept on paper or in chats."],
    ["FaFileInvoiceDollar", "Unclear dues", "What insurers and patients owe, and what is overdue, is hard to see."]];
  for (let i = 0; i < 3; i++) {
    const y = 1.5 + i * 1.15; card(s, 4.6, y, 4.8, 0.98);
    await circle(s, pains[i][0], 4.8, y + 0.19, 0.6, H.deep, H.mint, pains[i][1]);
    T(s, pains[i][1], { x: 5.6, y: y + 0.12, w: 3.7, h: 0.35, fontSize: 16, bold: true, color: C.text2 });
    T(s, pains[i][2], { x: 5.6, y: y + 0.47, w: 3.7, h: 0.45, fontSize: 14, valign: "top" });
  }
  s.addNotes("Ask the room: which department's margin can you state today, without calling the accountant?");

  // 3. Six issues
  s = light("Six issues slowing hospital finance", "The problem");
  const issues = [["FaHospital", "Department blind spots", "Daily revenue and cost by department is not visible."],
    ["FaUserMd", "Doctor commissions", "Referral and consulting payouts are tracked by hand."],
    ["FaFileInvoiceDollar", "Insurer and patient dues", "Receivables are hard to see, and harder to chase."],
    ["FaBoxes", "Supplier payables", "Medicine and consumable bills pile up unpaid."],
    ["FaUniversity", "Scattered bank accounts", "Cash and bank balances are spread across accounts."],
    ["FaHourglassHalf", "Late month-end numbers", "Management learns the result only after closing."]];
  for (let i = 0; i < 6; i++) {
    const col = i % 2, row = Math.floor(i / 2), x = 0.6 + col * 4.5, y = 1.45 + row * 1.2;
    card(s, x, y, 4.3, 1.08);
    await circle(s, issues[i][0], x + 0.25, y + 0.24, 0.6, H.teal, H.white, issues[i][1]);
    T(s, issues[i][1], { x: x + 1.1, y: y + 0.14, w: 3.0, h: 0.3, fontSize: 16, bold: true, color: C.text2 });
    T(s, issues[i][2], { x: x + 1.1, y: y + 0.46, w: 3.0, h: 0.52, fontSize: 14, valign: "top" });
  }

  // 4. What we solve
  s = light("What Finday changes for your hospital", "The solution");
  const fixes = [["Department blind spots", "Daily entries by department, with trends and margins"],
    ["Doctor commissions", "Referral and consulting commissions per doctor, due and paid"],
    ["Insurer and patient dues", "Receivables with aging, recorded against each payment"],
    ["Supplier payables", "Vendor ledgers and payables with aging"],
    ["Scattered bank accounts", "Bank balances and transfers across accounts in one place"],
    ["Late month-end numbers", "P&L, Balance Sheet and Cash Flow from the same daily entries"]];
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
  const steps = [["1", "Log daily", "Record each day's OPD, IPD, pharmacy and lab revenue, and the costs behind it."],
    ["2", "Finday organizes", "Ledgers, doctor commissions, dues and margins update from those entries."],
    ["3", "Decide faster", "See which departments lead, which costs are rising, and what is overdue."]];
  steps.forEach((st, i) => {
    const x = 0.6 + i * 3.05, col = i == 2 ? H.amber : H.teal;
    s.addShape(pres.ShapeType.ellipse, { x, y: 1.6, w: 0.8, h: 0.8, fill: { color: col }, line: { color: col }, objectName: "step " + st[0] });
    T(s, st[0], { x, y: 1.6, w: 0.8, h: 0.8, fontSize: 26, bold: true, color: i == 2 ? C.text2 : C.background1, align: "center", valign: "middle", fontFace: "Cambria" });
    if (i < 2) s.addShape(pres.ShapeType.line, { x: x + 0.95, y: 2.0, w: 1.95, h: 0, line: { color: H.mint, width: 2, dashType: "dash" } });
    T(s, st[1], { x, y: 2.7, w: 2.7, h: 0.5, fontSize: 22, bold: true, color: C.text2, fontFace: "Cambria" });
    T(s, st[2], { x, y: 3.25, w: 2.7, h: 1.4, fontSize: 15, valign: "top" });
  });

  // 6. Features
  s = light("Built around hospital operations", "The solution");
  const feats = [["FaChartPie", "Department reporting", "Revenue, costs and margins by department, category and period."],
    ["FaUserMd", "Doctor commissions", "Commission entries per doctor or referrer, with payouts due and paid."],
    ["FaFileInvoiceDollar", "Dues and payables", "Customer and vendor ledgers, with receivables and payables aging."],
    ["FaFileAlt", "Management reports", "P&L, Balance Sheet, Cash Flow and GST summary, as PDF, Excel or CSV."]];
  for (let i = 0; i < 4; i++) {
    const col = i % 2, row = Math.floor(i / 2), x = 0.6 + col * 4.5, y = 1.5 + row * 1.85;
    card(s, x, y, 4.3, 1.65);
    await circle(s, feats[i][0], x + 0.25, y + 0.3, 0.7, H.deep, H.mint, feats[i][1]);
    T(s, feats[i][1], { x: x + 1.2, y: y + 0.25, w: 2.9, h: 0.4, fontSize: 16, bold: true, color: C.text2 });
    T(s, feats[i][2], { x: x + 1.2, y: y + 0.7, w: 2.9, h: 0.85, fontSize: 14, valign: "top" });
  }

  // 7. Chart (demo data)
  s = light("Six months of hospital revenue", "Proof");
  s.addChart(pres.charts.BAR, [{ name: "Revenue (lakh)", labels: ["Apr", "May", "Jun", "Jul", "Aug", "Sep"], values: [39.5, 39.3, 32.8, 30.6, 34.8, 31.8] }],
    { x: 0.6, y: 1.4, w: 5.6, h: 3.6, chartColors: [H.teal], showTitle: true, title: "Monthly revenue, INR lakh", titleFontSize: 14, titleColor: H.deep, titleFontFace: "+mn-lt",
      showValue: true, dataLabelPosition: "outEnd", dataLabelFontSize: 12, dataLabelFormatCode: "0.0", dataLabelColor: H.ink, dataLabelFontFace: "+mn-lt",
      catAxisLabelColor: H.slate, valAxisLabelColor: H.slate, catAxisLabelFontFace: "+mn-lt", valAxisLabelFontFace: "+mn-lt", catAxisLabelFontSize: 12, valAxisLabelFontSize: 12,
      valGridLine: { color: "D5E3E1", size: 0.5 }, catGridLine: { style: "none" }, showLegend: false, valAxisMinVal: 0 });
  [["INR 31.8L", "September revenue"], ["68.4%", "net margin"], ["183", "bills in the month"]].forEach((k, i) => {
    const y = 1.4 + i * 1.25; card(s, 6.6, y, 2.8, 1.1);
    T(s, k[0], { x: 6.85, y: y + 0.1, w: 2.4, h: 0.6, fontSize: 28, bold: true, fontFace: "Cambria", color: C.accent4 });
    T(s, k[1], { x: 6.85, y: y + 0.7, w: 2.4, h: 0.3, fontSize: 14, color: C.accent3 });
  });
  T(s, "Illustrative demo data from a multi-department hospital.", { x: 0.6, y: 5.05, w: 8, h: 0.3, fontSize: 11, color: C.accent3, italic: true });
  s.addNotes("Demo figures are illustrative. Replace with the hospital's own data during the live demo.");

  // 8. Data choice (dark)
  s = dark("Trust");
  T(s, "Your data, your choice", { x: 0.7, y: 0.4, w: 8, h: 0.8, fontSize: 36, bold: true, fontFace: "Cambria", color: C.background1 });
  const opts = [["FaGoogleDrive", "Your own Google Drive", ["Financial records in a Google Sheet in your Drive", "Finday does not keep a copy of it", "Open, export or delete the Sheet yourself", "Revoke Finday's access at any time"]],
    ["FaDatabase", "Finday managed database", ["No Google account needed", "Managed, secure storage for your hospital", "Records kept separate for your hospital", "Switch storage later through an approved request"]]];
  for (let i = 0; i < 2; i++) {
    const x = 0.7 + i * 4.4;
    s.addShape(pres.ShapeType.roundRect, { x, y: 1.5, w: 4.1, h: 3.4, rectRadius: 0.12, fill: { color: H.panel }, line: { color: H.panel } });
    await circle(s, opts[i][0], x + 0.3, 1.75, 0.7, H.amber, H.deep, opts[i][1]);
    T(s, opts[i][1], { x: x + 1.15, y: 1.75, w: 2.8, h: 0.7, fontSize: 18, bold: true, color: C.background1, valign: "middle" });
    T(s, opts[i][2].map((t, j) => ({ text: t, options: { bullet: true, breakLine: j < 3 } })), { x: x + 0.3, y: 2.7, w: 3.6, h: 2.0, fontSize: 14, color: C.background2, valign: "top", paraSpaceAfter: 8 });
  }
  T(s, "Finday is built for financial records. It does not hold patient clinical records.", { x: 0.7, y: 5.05, w: 8.6, h: 0.3, fontSize: 12, color: H.mint, italic: true });

  // 9. Security
  s = light("Security designed for hospital finance", "Trust");
  const sec = [["FaLock", "Separate workspace", "Your hospital's data is kept separate from every other organization."],
    ["FaUserShield", "Role-based access", "Owners, admins, managers and members see only what their role needs."],
    ["FaShieldAlt", "Encrypted connections", "Data travels over HTTPS. Sign-in uses password rules and email codes."],
    ["FaFilePdf", "Always portable", "Export statements as PDF and data as Excel or CSV whenever needed."]];
  for (let i = 0; i < 4; i++) {
    const col = i % 2, row = Math.floor(i / 2), x = 0.6 + col * 4.5, y = 1.5 + row * 1.85;
    card(s, x, y, 4.3, 1.65);
    await circle(s, sec[i][0], x + 0.25, y + 0.3, 0.7, H.teal, H.white, sec[i][1]);
    T(s, sec[i][1], { x: x + 1.2, y: y + 0.25, w: 2.9, h: 0.4, fontSize: 16, bold: true, color: C.text2 });
    T(s, sec[i][2], { x: x + 1.2, y: y + 0.7, w: 2.9, h: 0.85, fontSize: 14, valign: "top" });
  }
  T(s, "Hospital compliance requirements should be reviewed with us before go-live.", { x: 0.6, y: 5.08, w: 8.6, h: 0.3, fontSize: 11, color: C.accent3, italic: true });

  // 10. Comparison table
  s = light("Why not stay with spreadsheets?", "Comparison");
  const hdr = (t) => ({ text: t, options: { bold: true, color: H.white, fill: { color: H.deep }, align: "center", valign: "middle" } });
  const cell = (t, o = {}) => ({ text: t, options: Object.assign({ align: "center", valign: "middle" }, o) });
  const yes = (t) => cell(t, { bold: true, color: H.deep });
  const rows = [
    [{ text: "What your hospital needs", options: { bold: true, color: H.white, fill: { color: H.deep }, valign: "middle" } }, hdr("Spreadsheets"), hdr("Finday")],
    [cell("Daily entry by department", { align: "left" }), cell("Manual"), yes("Yes")],
    [cell("Doctor commission tracking", { align: "left" }), cell("Manual"), yes("Yes")],
    [cell("Insurer and patient dues with aging", { align: "left" }), cell("Manual"), yes("Yes")],
    [cell("P&L, Balance Sheet, Cash Flow", { align: "left" }), cell("Manual"), yes("Yes")],
    [cell("Live dashboard for management", { align: "left" }), cell("Manual"), yes("Yes")],
    [cell("Data in your own Google Drive", { align: "left" }), cell("Yes"), yes("Yes")],
  ];
  s.addTable(rows, { x: 0.6, y: 1.4, w: 8.8, colW: [4.8, 2.0, 2.0], rowH: 0.42, fontFace: "Calibri", fontSize: 14, color: C.text1, border: { type: "solid", pt: 0.5, color: "CBD9D6" } });
  T(s, "Finday covers financial operations. Clinical record-keeping is outside its scope.", { x: 0.6, y: 4.9, w: 8.8, h: 0.3, fontSize: 11, color: C.accent3, italic: true });

  // 11. Plans
  s = light("Plans for hospitals", "Pricing");
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
  s.addNotes("Prices are indicative. Confirm the final figures, GST treatment and storage option for the hospital before the meeting.");

  // 12. Next steps (dark)
  s = dark("Next steps");
  T(s, "See your hospital's numbers in one place", { x: 0.7, y: 0.9, w: 8.2, h: 1.4, fontSize: 36, bold: true, fontFace: "Cambria", color: C.background1, valign: "top" });
  const nxt = ["Book a demo built around your departments and doctors", "Start a 14-day trial and connect your Google Drive", "Set up departments, doctors and bank accounts with us"];
  nxt.forEach((t, i) => {
    const y = 2.6 + i * 0.85;
    s.addShape(pres.ShapeType.ellipse, { x: 0.7, y, w: 0.55, h: 0.55, fill: { color: H.amber }, line: { color: H.amber }, objectName: "next " + (i + 1) });
    T(s, String(i + 1), { x: 0.7, y, w: 0.55, h: 0.55, fontSize: 18, bold: true, color: C.text2, align: "center", valign: "middle", fontFace: "Cambria" });
    T(s, t, { x: 1.5, y, w: 7.2, h: 0.55, fontSize: 18, color: C.background2, valign: "middle" });
  });
  s.addNotes("Close by agreeing the first step, the demo date, and who at the hospital will connect the Google Drive and share the department list.");

  await pres.writeFile({ fileName: OUT });
  await applyTheme(OUT, THEME);
  console.log("written", OUT);
})().catch(e => { console.error(e); process.exit(1); });
