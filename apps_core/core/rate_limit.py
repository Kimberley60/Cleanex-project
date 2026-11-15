import hashlib
import hmac
import logging
from functools import wraps

from django.conf import settings
from django.core.cache import cache
from django.shortcuts import render


logger = logging.getLogger(__name__)


RATE_LIMIT_TEMPLATE = "errors/429.html"
COUNTER_RECOVERY_ATTEMPTS = 3


def _digest(value):
    return hmac.new(
        str(settings.SECRET_KEY).encode("utf-8"),
        value.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()


def _client_ip(request):
    return request.META.get("REMOTE_ADDR") or "unknown"


def _normalize_identifier(value):
    if not isinstance(value, str):
        return ""
    return value.strip().lower()


def _ip_identifier(request):
    return _client_ip(request)


def _ip_field_identifier(request, field):
    normalized = _normalize_identifier(request.POST.get(field))
    if not normalized:
        return None
    return f"{_client_ip(request)}:{normalized}"


def _user_identifier(request):
    user = getattr(request, "user", None)
    if user is not None and user.is_authenticated:
        return f"user:{user.pk}"
    return None


class RateLimitScope:
    def __init__(self, name, limit, window, identifier):
        self.name = name
        self.limit = limit
        self.window = window
        self.identifier = identifier


def ip_scope(endpoint, limit, window):
    return RateLimitScope(
        name=f"{endpoint}:ip",
        limit=limit,
        window=window,
        identifier=_ip_identifier,
    )


def ip_identifier_scope(endpoint, limit, window, field):
    return RateLimitScope(
        name=f"{endpoint}:ip-{field}",
        limit=limit,
        window=window,
        identifier=lambda request: _ip_field_identifier(request, field),
    )


def ip_booking_scope(endpoint, limit, window, kwarg="booking_id"):
    def identifier(request):
        resolver_match = getattr(request, "resolver_match", None)
        kwargs = getattr(resolver_match, "kwargs", None) or {}
        booking_id = kwargs.get(kwarg)
        if booking_id is None:
            return None
        return f"{_client_ip(request)}:{booking_id}"

    return RateLimitScope(
        name=f"{endpoint}:ip-{kwarg}",
        limit=limit,
        window=window,
        identifier=identifier,
    )


def user_scope(endpoint, limit, window):
    return RateLimitScope(
        name=f"{endpoint}:user",
        limit=limit,
        window=window,
        identifier=_user_identifier,
    )


CONTACT_RATE_LIMITS = (
    ip_scope("contact", 5, 3600),
    ip_identifier_scope("contact", 5, 86400, "email"),
)

QUOTE_RATE_LIMITS = (
    ip_scope("quote", 10, 3600),
    ip_identifier_scope("quote", 5, 86400, "email"),
    user_scope("quote", 20, 86400),
)

BOOKING_RATE_LIMITS = (
    ip_scope("booking", 10, 3600),
    ip_identifier_scope("booking", 5, 86400, "email"),
    user_scope("booking", 10, 86400),
)

CUSTOMER_REGISTER_RATE_LIMITS = (
    ip_scope("customer_register", 5, 3600),
    ip_identifier_scope("customer_register", 3, 86400, "email"),
)

CUSTOMER_RESEND_VERIFICATION_RATE_LIMITS = (
    ip_scope("customer_resend_verification", 5, 3600),
    ip_identifier_scope("customer_resend_verification", 3, 86400, "email"),
)

LOGIN_RATE_LIMITS = (
    ip_scope("login", 20, 3600),
    ip_identifier_scope("login", 10, 3600, "username"),
)

PASSWORD_RESET_RATE_LIMITS = (
    ip_scope("password_reset", 5, 3600),
    ip_identifier_scope("password_reset", 3, 86400, "email"),
)


def cache_key(scope_name, identifier):
    return f"public-rate-limit:{scope_name}:{_digest(identifier)}"


def _increment_counter(cache_key, timeout):
    for _ in range(COUNTER_RECOVERY_ATTEMPTS):
        try:
            if cache.add(cache_key, 1, timeout):
                return 1
            return cache.incr(cache_key)
        except ValueError:
            continue
        except Exception:
            logger.warning("Public rate-limit cache failed.")
            return None

    logger.warning(
        "Public rate-limit counter expired during recovery."
    )
    return None


def _request_from_args(args):
    for arg in args:
        if hasattr(arg, "method") and hasattr(arg, "META"):
            return arg
    return args[0] if args else None


def rate_limited(*scopes):
    def decorator(view_func):
        @wraps(view_func)
        def wrapper(*args, **kwargs):
            request = _request_from_args(args)
            if request is None or request.method != "POST":
                return view_func(*args, **kwargs)

            exceeded = False
            for scope in scopes:
                identifier = scope.identifier(request)
                if identifier is None:
                    continue

                attempts = _increment_counter(
                    cache_key(scope.name, identifier),
                    scope.window,
                )
                if attempts is not None and attempts > scope.limit:
                    exceeded = True

            if exceeded:
                return render(
                    request,
                    RATE_LIMIT_TEMPLATE,
                    status=429,
                )

            return view_func(*args, **kwargs)

        return wrapper

    return decorator
