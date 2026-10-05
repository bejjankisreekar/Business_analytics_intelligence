from django import forms
from django.utils.html import format_html


class SearchableChoiceWidget(forms.Select):
    """
    A custom widget that renders a select field as a searchable dropdown (combobox).
    Uses the initCombobox JavaScript function to provide filtering/search functionality.
    """

    template_name = 'core/widgets/searchable_choice.html'

    def __init__(self, attrs=None, choices=(), search_placeholder="Search..."):
        super().__init__(attrs, choices)
        self.search_placeholder = search_placeholder

    def get_context(self, name, value, attrs):
        context = super().get_context(name, value, attrs)
        context['search_placeholder'] = self.search_placeholder
        context['widget_id'] = attrs.get('id', '') if attrs else ''
        return context


class SearchableModelChoiceWidget(forms.Select):
    """
    A custom widget for ModelChoiceField that renders as a searchable dropdown.
    """

    template_name = 'core/widgets/searchable_choice.html'

    def __init__(self, attrs=None, choices=(), search_placeholder="Search..."):
        super().__init__(attrs, choices)
        self.search_placeholder = search_placeholder

    def get_context(self, name, value, attrs):
        context = super().get_context(name, value, attrs)
        context['search_placeholder'] = self.search_placeholder
        context['widget_id'] = attrs.get('id', '') if attrs else ''
        return context
