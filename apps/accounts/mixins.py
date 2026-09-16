from django.contrib.auth.mixins import LoginRequiredMixin
from django.core.exceptions import PermissionDenied

from .models import User


class SuperAdminRequiredMixin(LoginRequiredMixin):
    login_url = "accounts:login"

    def dispatch(self, request, *args, **kwargs):
        if request.user.is_authenticated and request.user.role != User.Role.SUPER_ADMIN:
            raise PermissionDenied
        return super().dispatch(request, *args, **kwargs)
