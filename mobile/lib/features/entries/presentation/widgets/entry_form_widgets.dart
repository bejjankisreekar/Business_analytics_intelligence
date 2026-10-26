import 'package:flutter/material.dart';

import '../../../../app/theme/app_colors.dart';
import '../../../../core/utils/date_formatter.dart';

/// White rounded card with an optional small heading, used to group fields on the entry form.
class FormCard extends StatelessWidget {
  const FormCard({super.key, this.title, this.icon, required this.children});

  final String? title;
  final IconData? icon;
  final List<Widget> children;

  @override
  Widget build(BuildContext context) => Container(
    width: double.infinity,
    margin: const EdgeInsets.only(top: 14),
    padding: const EdgeInsets.all(16),
    decoration: BoxDecoration(
      color: AppColors.card,
      borderRadius: BorderRadius.circular(20),
      boxShadow: AppColors.cardShadow,
    ),
    child: Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        if (title != null)
          Padding(
            padding: const EdgeInsets.only(bottom: 12),
            child: Row(
              children: [
                if (icon != null) ...[
                  Icon(icon, size: 16, color: AppColors.textSecondary),
                  const SizedBox(width: 6),
                ],
                Text(
                  title!.toUpperCase(),
                  style: const TextStyle(
                    fontSize: 11,
                    fontWeight: FontWeight.w800,
                    letterSpacing: 0.6,
                    color: AppColors.textSecondary,
                  ),
                ),
              ],
            ),
          ),
        ...children,
      ],
    ),
  );
}

/// A field that looks like a dropdown and opens a searchable bottom sheet.
class PickerField extends StatelessWidget {
  const PickerField({
    super.key,
    required this.label,
    required this.options,
    required this.selected,
    required this.onChanged,
    this.icon,
    this.error,
    this.clearable = false,
    this.placeholder = 'Select',
    this.emptyHint,
    this.accent = AppColors.brand,
  });

  final String label;

  /// Display names keyed by the value they select.
  final Map<String, String> options;
  final String? selected;
  final ValueChanged<String?> onChanged;
  final IconData? icon;
  final String? error;
  final bool clearable;
  final String placeholder;

  /// Shown instead of opening the sheet when there are no options.
  final String? emptyHint;
  final Color accent;

  Future<void> _open(BuildContext context) async {
    if (options.isEmpty) {
      if (emptyHint != null) {
        ScaffoldMessenger.of(context)
            .showSnackBar(SnackBar(content: Text(emptyHint!)));
      }
      return;
    }
    final result = await showModalBottomSheet<_Pick>(
      context: context,
      isScrollControlled: true,
      useSafeArea: true,
      backgroundColor: AppColors.card,
      shape: const RoundedRectangleBorder(
        borderRadius: BorderRadius.vertical(top: Radius.circular(24)),
      ),
      builder: (_) => _PickerSheet(
        title: label,
        options: options,
        selected: selected,
        clearable: clearable,
        accent: accent,
      ),
    );
    if (result != null) onChanged(result.value);
  }

  @override
  Widget build(BuildContext context) {
    final text = options[selected];
    final hasError = error != null;
    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        InkWell(
          borderRadius: BorderRadius.circular(14),
          onTap: () => _open(context),
          child: InputDecorator(
            decoration: InputDecoration(
              labelText: label,
              floatingLabelBehavior: FloatingLabelBehavior.always,
              prefixIcon: icon == null ? null : Icon(icon, size: 20),
              suffixIcon: const Icon(Icons.keyboard_arrow_down),
              errorText: error,
              contentPadding: const EdgeInsets.symmetric(
                horizontal: 12,
                vertical: 14,
              ),
            ),
            child: Text(
              text ?? placeholder,
              overflow: TextOverflow.ellipsis,
              style: TextStyle(
                fontSize: 15,
                fontWeight: FontWeight.w600,
                color: text == null
                    ? (hasError ? AppColors.danger : AppColors.textSecondary)
                    : AppColors.textPrimary,
              ),
            ),
          ),
        ),
      ],
    );
  }
}

class _Pick {
  const _Pick(this.value);
  final String? value;
}

class _PickerSheet extends StatefulWidget {
  const _PickerSheet({
    required this.title,
    required this.options,
    required this.selected,
    required this.clearable,
    required this.accent,
  });

  final String title;
  final Map<String, String> options;
  final String? selected;
  final bool clearable;
  final Color accent;

  @override
  State<_PickerSheet> createState() => _PickerSheetState();
}

class _PickerSheetState extends State<_PickerSheet> {
  String _query = '';

  @override
  Widget build(BuildContext context) {
    final entries = widget.options.entries
        .where(
          (e) => e.value.toLowerCase().contains(_query.trim().toLowerCase()),
        )
        .toList();
    final searchable = widget.options.length > 7;
    return Padding(
      padding: EdgeInsets.only(
        bottom: MediaQuery.of(context).viewInsets.bottom,
      ),
      child: ConstrainedBox(
        constraints: BoxConstraints(
          maxHeight: MediaQuery.of(context).size.height * 0.75,
        ),
        child: Column(
          mainAxisSize: MainAxisSize.min,
          children: [
            Container(
              width: 40,
              height: 4,
              margin: const EdgeInsets.only(top: 10, bottom: 14),
              decoration: BoxDecoration(
                color: AppColors.divider,
                borderRadius: BorderRadius.circular(4),
              ),
            ),
            Padding(
              padding: const EdgeInsets.fromLTRB(20, 0, 12, 8),
              child: Row(
                children: [
                  Expanded(
                    child: Text(
                      widget.title,
                      style: const TextStyle(
                        fontSize: 18,
                        fontWeight: FontWeight.w800,
                      ),
                    ),
                  ),
                  if (widget.clearable && widget.selected != null)
                    TextButton(
                      onPressed: () =>
                          Navigator.pop(context, const _Pick(null)),
                      child: const Text('Clear'),
                    ),
                ],
              ),
            ),
            if (searchable)
              Padding(
                padding: const EdgeInsets.fromLTRB(16, 0, 16, 8),
                child: TextField(
                  decoration: const InputDecoration(
                    hintText: 'Search',
                    prefixIcon: Icon(Icons.search),
                    isDense: true,
                  ),
                  onChanged: (v) => setState(() => _query = v),
                ),
              ),
            Flexible(
              child: entries.isEmpty
                  ? const Padding(
                      padding: EdgeInsets.all(32),
                      child: Text(
                        'Nothing matches.',
                        style: TextStyle(color: AppColors.textSecondary),
                      ),
                    )
                  : ListView.builder(
                      shrinkWrap: true,
                      padding: const EdgeInsets.fromLTRB(12, 0, 12, 20),
                      itemCount: entries.length,
                      itemBuilder: (_, i) {
                        final e = entries[i];
                        final on = e.key == widget.selected;
                        return ListTile(
                          shape: RoundedRectangleBorder(
                            borderRadius: BorderRadius.circular(14),
                          ),
                          tileColor: on
                              ? widget.accent.withValues(alpha: 0.08)
                              : null,
                          title: Text(
                            e.value,
                            style: TextStyle(
                              fontWeight: on
                                  ? FontWeight.w800
                                  : FontWeight.w600,
                            ),
                          ),
                          trailing: on
                              ? Icon(Icons.check_circle, color: widget.accent)
                              : null,
                          onTap: () => Navigator.pop(context, _Pick(e.key)),
                        );
                      },
                    ),
            ),
          ],
        ),
      ),
    );
  }
}

/// Date selector: previous/next arrows around a tappable date, with Today / Yesterday shortcuts.
class DateStrip extends StatelessWidget {
  const DateStrip({
    super.key,
    required this.date,
    required this.onChanged,
    required this.onPick,
    this.error,
    this.accent = AppColors.brand,
  });

  final DateTime date;
  final ValueChanged<DateTime> onChanged;
  final VoidCallback onPick;
  final String? error;
  final Color accent;

  static DateTime _day(DateTime d) => DateTime(d.year, d.month, d.day);

  @override
  Widget build(BuildContext context) {
    final today = _day(DateTime.now());
    final current = _day(date);
    final diff = today.difference(current).inDays;
    final relative = switch (diff) {
      0 => 'Today',
      1 => 'Yesterday',
      -1 => 'Tomorrow',
      _ => weekdayName(current),
    };
    return FormCard(
      children: [
        Row(
          children: [
            IconButton.filledTonal(
              onPressed: () =>
                  onChanged(current.subtract(const Duration(days: 1))),
              icon: const Icon(Icons.chevron_left),
            ),
            Expanded(
              child: InkWell(
                borderRadius: BorderRadius.circular(14),
                onTap: onPick,
                child: Padding(
                  padding: const EdgeInsets.symmetric(vertical: 4),
                  child: Column(
                    children: [
                      Text(
                        relative.toUpperCase(),
                        style: TextStyle(
                          fontSize: 11,
                          fontWeight: FontWeight.w800,
                          letterSpacing: 0.6,
                          color: accent,
                        ),
                      ),
                      const SizedBox(height: 2),
                      FittedBox(
                        fit: BoxFit.scaleDown,
                        child: Row(
                          children: [
                            const Icon(
                              Icons.calendar_today,
                              size: 15,
                              color: AppColors.textSecondary,
                            ),
                            const SizedBox(width: 6),
                            Text(
                              displayDate(current),
                              style: const TextStyle(
                                fontSize: 17,
                                fontWeight: FontWeight.w800,
                              ),
                            ),
                          ],
                        ),
                      ),
                    ],
                  ),
                ),
              ),
            ),
            IconButton.filledTonal(
              onPressed: () => onChanged(current.add(const Duration(days: 1))),
              icon: const Icon(Icons.chevron_right),
            ),
          ],
        ),
        const SizedBox(height: 10),
        Row(
          children: [
            _Shortcut(
              label: 'Today',
              selected: diff == 0,
              accent: accent,
              onTap: () => onChanged(today),
            ),
            const SizedBox(width: 8),
            _Shortcut(
              label: 'Yesterday',
              selected: diff == 1,
              accent: accent,
              onTap: () => onChanged(today.subtract(const Duration(days: 1))),
            ),
          ],
        ),
        if (error != null)
          Padding(
            padding: const EdgeInsets.only(top: 8),
            child: Text(
              error!,
              style: const TextStyle(color: AppColors.danger, fontSize: 13),
            ),
          ),
      ],
    );
  }
}

String weekdayName(DateTime d) => const [
  'Monday',
  'Tuesday',
  'Wednesday',
  'Thursday',
  'Friday',
  'Saturday',
  'Sunday',
][d.weekday - 1];

class _Shortcut extends StatelessWidget {
  const _Shortcut({
    required this.label,
    required this.selected,
    required this.accent,
    required this.onTap,
  });

  final String label;
  final bool selected;
  final Color accent;
  final VoidCallback onTap;

  @override
  Widget build(BuildContext context) => ChoiceChip(
    label: Text(label),
    selected: selected,
    showCheckmark: false,
    selectedColor: accent,
    labelStyle: TextStyle(
      fontWeight: FontWeight.w700,
      color: selected ? Colors.white : AppColors.textPrimary,
    ),
    onSelected: (_) => onTap(),
  );
}
