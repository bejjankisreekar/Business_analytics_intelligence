from django import forms

from .models import ContactMessage


class ContactForm(forms.ModelForm):
    message = forms.CharField(max_length=240, widget=forms.Textarea(attrs={"rows": 4}))

    class Meta:
        model = ContactMessage
        fields = ["name", "organization_name", "phone", "message"]
