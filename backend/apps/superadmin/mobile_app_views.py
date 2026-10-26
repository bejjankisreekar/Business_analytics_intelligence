"""Upload, switch and delete the Android app (.apk) that clients download from
their Profile page. Stored per environment, like the rest of the console."""
import hashlib

from django.contrib import messages
from django.db import transaction
from django.shortcuts import get_object_or_404, redirect
from django.views.generic import TemplateView, View

from apps.accounts.mixins import SuperAdminRequiredMixin

from .models import MobileAppVersion
from .views import _env_label_or_404

MAX_APK_BYTES = 100 * 1024 * 1024


def _versions(env):
    return MobileAppVersion.objects.using(env).defer("file")


def _make_current(env, version):
    """Make `version` the only current one."""
    MobileAppVersion.objects.using(env).exclude(pk=version.pk).update(is_current=False)
    MobileAppVersion.objects.using(env).filter(pk=version.pk).update(is_current=True)


class MobileAppListView(SuperAdminRequiredMixin, TemplateView):
    template_name = "superadmin/mobile_app.html"

    def get_context_data(self, **kwargs):
        env = kwargs["env"]
        context = super().get_context_data(**kwargs)
        context["env_key"] = env
        context["env_label"] = _env_label_or_404(env)
        context["versions"] = list(_versions(env))
        context["max_mb"] = MAX_APK_BYTES // (1024 * 1024)
        return context


class MobileAppUploadView(SuperAdminRequiredMixin, View):
    def post(self, request, env):
        _env_label_or_404(env)
        back = redirect("superadmin:mobile_app", env=env)
        version = request.POST.get("version", "").strip()
        notes = request.POST.get("notes", "").strip()[:500]
        upload = request.FILES.get("file")
        if not version or len(version) > 40:
            messages.error(request, "Enter a version number, e.g. 1.0.3.")
            return back
        if upload is None:
            messages.error(request, "Choose the .apk file to upload.")
            return back
        if not upload.name.lower().endswith(".apk"):
            messages.error(request, "Only .apk files can be uploaded.")
            return back
        if upload.size > MAX_APK_BYTES:
            messages.error(request, f"That file is too large (limit {MAX_APK_BYTES // (1024 * 1024)} MB).")
            return back
        data = upload.read()
        if data[:2] != b"PK":  # an APK is a zip archive
            messages.error(request, "That doesn't look like a valid APK file.")
            return back
        if _versions(env).filter(version=version).exists():
            messages.error(request, f"Version {version} is already uploaded. Delete it first or use a new version number.")
            return back
        with transaction.atomic(using=env):
            created = MobileAppVersion.objects.using(env).create(
                version=version, notes=notes, file=data, file_name=upload.name[:120], file_size=len(data),
                sha256=hashlib.sha256(data).hexdigest(), uploaded_by_email=request.user.email,
            )
            _make_current(env, created)
        messages.success(request, f"Uploaded mobile app {version}; clients now download this version.")
        return back


class MobileAppMakeCurrentView(SuperAdminRequiredMixin, View):
    def post(self, request, env, pk):
        _env_label_or_404(env)
        version = get_object_or_404(_versions(env), pk=pk)
        _make_current(env, version)
        messages.success(request, f"Mobile app {version.version} is now the version clients download.")
        return redirect("superadmin:mobile_app", env=env)


class MobileAppDeleteView(SuperAdminRequiredMixin, View):
    def post(self, request, env, pk):
        _env_label_or_404(env)
        version = get_object_or_404(_versions(env), pk=pk)
        was_current = version.is_current
        with transaction.atomic(using=env):
            MobileAppVersion.objects.using(env).filter(pk=version.pk).delete()
            if was_current:
                newest = _versions(env).first()
                if newest is not None:
                    _make_current(env, newest)
        messages.success(request, f"Deleted mobile app {version.version}.")
        return redirect("superadmin:mobile_app", env=env)
