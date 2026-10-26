import 'package:flutter/material.dart';

import '../../app/theme/app_colors.dart';
import '../../core/utils/date_formatter.dart';
import 'branded_refresh.dart';
import 'report_widgets.dart';

/// The blueprint every report screen follows, top to bottom:
///
///  1. [TabPills]   – what you are looking at (Revenue / Cost, P&L / Balance, …)
///  2. filters      – labelled dropdowns in one [FilterRow] (Range, View by, Category, …)
///  3. a [HeroCard] – the headline number for the current selection
///  4. sections     – [SectionTitle] + [ReportCard] / list of cards
///
/// Wrap the screen in [ReportPage] so the app bar, pull-to-refresh and spacing are identical
/// everywhere. Add new report screens by filling those slots, not by inventing new controls.

/// Periods offered by every period dropdown; keys match the backend's `PERIOD_CHOICES`.
const kReportPeriods = {
  'today': 'Today',
  'this_week': 'This week',
  'this_month': 'This month',
  'last_month': 'Last month',
  'this_quarter': 'This quarter',
  'this_fy': 'This financial year',
  'last_fy': 'Last financial year',
  'year_to_date': 'Year to date',
};

const kGranularities = {
  'daily': 'Daily',
  'weekly': 'Weekly',
  'monthly': 'Monthly',
  'quarterly': 'Quarterly',
  'yearly': 'Yearly',
};

/// One option of a [TabPills] row.
class TabPillItem<T> {
  const TabPillItem({
    required this.value,
    required this.label,
    required this.icon,
    this.color = AppColors.brand,
  });

  final T value;
  final String label;
  final IconData icon;

  /// Fill used when this pill is selected.
  final Color color;
}

/// Equal-width pill tabs; the selected one is filled with its colour.
class TabPills<T> extends StatelessWidget {
  const TabPills({
    super.key,
    required this.items,
    required this.selected,
    required this.onChanged,
  });

  final List<TabPillItem<T>> items;
  final T selected;
  final ValueChanged<T> onChanged;

  @override
  Widget build(BuildContext context) => Row(
    children: [
      for (var i = 0; i < items.length; i++)
        Expanded(
          child: Padding(
            padding: EdgeInsets.only(right: i == items.length - 1 ? 0 : 8),
            child: _Pill(
              item: items[i],
              selected: items[i].value == selected,
              onTap: () => onChanged(items[i].value),
            ),
          ),
        ),
    ],
  );
}

class _Pill extends StatelessWidget {
  const _Pill({
    required this.item,
    required this.selected,
    required this.onTap,
  });

  final TabPillItem item;
  final bool selected;
  final VoidCallback onTap;

  @override
  Widget build(BuildContext context) => Material(
    color: selected ? item.color : AppColors.card,
    borderRadius: BorderRadius.circular(14),
    child: InkWell(
      borderRadius: BorderRadius.circular(14),
      onTap: onTap,
      child: Container(
        padding: const EdgeInsets.symmetric(vertical: 11, horizontal: 4),
        decoration: BoxDecoration(
          borderRadius: BorderRadius.circular(14),
          border: Border.all(color: selected ? item.color : AppColors.divider),
        ),
        child: FittedBox(
          fit: BoxFit.scaleDown,
          child: Row(
            children: [
              Icon(
                item.icon,
                size: 17,
                color: selected ? Colors.white : AppColors.textSecondary,
              ),
              const SizedBox(width: 6),
              Text(
                item.label,
                style: TextStyle(
                  fontWeight: FontWeight.w700,
                  color: selected ? Colors.white : AppColors.textPrimary,
                ),
              ),
            ],
          ),
        ),
      ),
    ),
  );
}

/// Dropdowns side by side, sharing the row equally. One child fills the whole row.
class FilterRow extends StatelessWidget {
  const FilterRow({super.key, required this.children});

  final List<Widget> children;

  @override
  Widget build(BuildContext context) => Row(
    children: [
      for (var i = 0; i < children.length; i++) ...[
        if (i > 0) const SizedBox(width: 10),
        Expanded(child: children[i]),
      ],
    ],
  );
}

/// The standard "Range" dropdown over [kReportPeriods].
class PeriodDropdown extends StatelessWidget {
  const PeriodDropdown({
    super.key,
    required this.value,
    required this.onChanged,
    this.label = 'Range',
  });

  /// A key of [kReportPeriods], or a custom range as `custom:YYYY-MM-DD:YYYY-MM-DD`.
  final String value;
  final ValueChanged<String> onChanged;
  final String label;

  static const customPrefix = 'custom:';

  /// The two dates of a custom [value], or null for a preset period.
  static DateTimeRange? parseCustom(String value) {
    if (!value.startsWith(customPrefix)) return null;
    final parts = value.split(':');
    if (parts.length != 3) return null;
    final from = DateTime.tryParse(parts[1]);
    final to = DateTime.tryParse(parts[2]);
    return from == null || to == null
        ? null
        : DateTimeRange(start: from, end: to);
  }

  Future<void> _pickCustom(BuildContext context) async {
    final picked = await showDateRangePicker(
      context: context,
      firstDate: DateTime(2000),
      lastDate: DateTime.now(),
      initialDateRange: parseCustom(value),
    );
    if (picked != null) {
      onChanged('$customPrefix${isoDate(picked.start)}:${isoDate(picked.end)}');
    }
  }

  @override
  Widget build(BuildContext context) {
    final custom = parseCustom(value);
    final customLabel = custom == null
        ? 'Custom range'
        : '${displayDate(custom.start)} – ${displayDate(custom.end)}';
    return DropdownField(
      label: label,
      icon: Icons.date_range_outlined,
      value: custom == null ? value : 'custom',
      items: {...kReportPeriods, 'custom': customLabel},
      onChanged: (v) {
        if (v == null) return;
        if (v == 'custom') {
          _pickCustom(context);
        } else {
          onChanged(v);
        }
      },
    );
  }
}

/// A tappable field shaped like [DropdownField], for opening a date-range picker.
class DateRangeField extends StatelessWidget {
  const DateRangeField({
    super.key,
    required this.label,
    required this.text,
    required this.onTap,
  });

  final String label;
  final String text;
  final VoidCallback onTap;

  @override
  Widget build(BuildContext context) => InkWell(
    borderRadius: BorderRadius.circular(14),
    onTap: onTap,
    child: InputDecorator(
      decoration: InputDecoration(
        labelText: label,
        prefixIcon: const Icon(Icons.date_range_outlined, size: 20),
        suffixIcon: const Icon(Icons.arrow_drop_down),
        filled: true,
        fillColor: AppColors.card,
        contentPadding: const EdgeInsets.symmetric(
          horizontal: 12,
          vertical: 14,
        ),
        border: OutlineInputBorder(
          borderRadius: BorderRadius.circular(14),
          borderSide: const BorderSide(color: AppColors.divider),
        ),
        enabledBorder: OutlineInputBorder(
          borderRadius: BorderRadius.circular(14),
          borderSide: const BorderSide(color: AppColors.divider),
        ),
      ),
      child: Text(
        text,
        style: const TextStyle(fontWeight: FontWeight.w600, fontSize: 15),
      ),
    ),
  );
}

/// Page frame for report screens: app bar, pull-to-refresh, then tabs, filters and body.
class ReportPage extends StatelessWidget {
  const ReportPage({
    super.key,
    required this.title,
    required this.onRefresh,
    required this.children,
    this.tabs,
    this.filters,
    this.actions,
  });

  final String title;
  final Future<void> Function() onRefresh;
  final List<Widget>? actions;

  /// Usually a [TabPills].
  final Widget? tabs;

  /// Usually a [FilterRow] (or several, stacked).
  final List<Widget>? filters;

  /// The hero card and sections (typically one `asyncBody`).
  final List<Widget> children;

  @override
  Widget build(BuildContext context) => Scaffold(
    appBar: AppBar(title: Text(title), actions: actions),
    body: BrandedRefresh(
      onRefresh: onRefresh,
      child: ListView(
        padding: const EdgeInsets.all(16),
        children: [
          ?tabs,
          for (final f in filters ?? const <Widget>[]) ...[
            if (tabs != null || f != filters!.first) const SizedBox(height: 12),
            f,
          ],
          if (tabs != null || (filters?.isNotEmpty ?? false))
            const SizedBox(height: 16),
          ...children,
        ],
      ),
    ),
  );
}
