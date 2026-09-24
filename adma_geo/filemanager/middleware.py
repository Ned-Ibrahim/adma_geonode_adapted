"""Site-wide login requirement.

ADMA is members only. Every request from an anonymous visitor is refused unless
its path is on the short exemption list below, so a view added later is private
by default instead of public by default.
"""
from urllib.parse import quote

from django.conf import settings
from django.http import HttpResponseRedirect, JsonResponse

# Paths an anonymous visitor may reach. Keep this list short.
#
# - The login and logout pages, or nobody could sign in.
# - /admin/ enforces its own staff login.
# - /api/v1/ authenticates with DRF tokens inside each view, which this
#   middleware cannot see; DRF's default IsAuthenticated permission guards it.
# - /auth/check/ answers nginx's auth_request subrequests with 204 or 401.
# - /static/ holds CSS and JS for the login page.
EXEMPT_PREFIXES = (
    '/accounts/login/',
    '/accounts/logout/',
    '/admin/',
    '/api/v1/',
    '/auth/check/',
    '/static/',
)


class LoginRequiredMiddleware:

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if request.user.is_authenticated or request.path.startswith(EXEMPT_PREFIXES):
            return self.get_response(request)
        if request.path.startswith('/api/'):
            return JsonResponse({'error': 'Authentication required'}, status=401)
        return HttpResponseRedirect(
            f'{settings.LOGIN_URL}?next={quote(request.get_full_path())}'
        )
