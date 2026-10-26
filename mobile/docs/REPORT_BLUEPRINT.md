# Report screen blueprint

Every report screen in the mobile app is built from the widgets in
`lib/shared/widgets/report_blueprint.dart`. Do not add new control styles; fill these slots.

| Slot | Widget | Use |
| --- | --- | --- |
| Page | `ReportPage` | App bar, pull-to-refresh, 16px padding, spacing between slots |
| Tabs | `TabPills<T>` + `TabPillItem` | What you are looking at (Revenue / Expense / Purchase, P&L / Balance, …). Selected pill is filled with its colour |
| Filters | `FilterRow` of `DropdownField` | Labelled dropdowns, equal width. `PeriodDropdown` for "Range" (`kReportPeriods` plus "Custom range", which opens a date-range picker and travels as `custom:from:to`), `DropdownField(items: kGranularities)` for "View by", `DateRangeField` for a date-range picker |
| Headline | `HeroCard` | One big number for the current selection, with up to three figures |
| Sections | `SectionTitle` + `ReportCard` / `BreakdownBars` / `LineRow` | The detail |

Order is always tabs, filters, headline, sections.

Screens using it: Intelligence, Monthly summary, Financial reports, Category statement,
Receivables & payables, Quantity detail, Daily performance (filters only), New entry (tabs only).

The New entry form has its own pieces in `features/entries/presentation/widgets/entry_form_widgets.dart`
(`FormCard`, `PickerField`, `DateStrip`); use `PickerField` instead of chips for long option lists.
A filter that only applies to one tab (for example Financial reports' cash/online "Show") is added to
the same `FilterRow` only while that tab is selected.

Adding a period or granularity: change `kReportPeriods` / `kGranularities` (keys must match the
backend `PERIOD_CHOICES` / `SUMMARY_GRANULARITIES`).
