from django.urls import path, re_path

from .views import (
    CustomerLoginView,
    CustomerLogoutView,
    CustomerPasswordResetCompleteView,
    CustomerPasswordResetConfirmView,
    CustomerPasswordResetDoneView,
    CustomerPasswordResetView,
    customer_dashboard,
    customer_profile,
    customer_quote_history,
    register_customer,
    registration_pending,
    resend_verification,
    verify_customer_email,
)


urlpatterns = [
    path("register/", register_customer, name="customer_register"),
    path(
        "registration-pending/",
        registration_pending,
        name="customer_registration_pending",
    ),
    re_path(
        r"^verify/(?P<uidb64>[-A-Za-z0-9_]+)/(?P<token>[-A-Za-z0-9_=]+)/?$",
        verify_customer_email,
        name="customer_verify",
    ),
    path(
        "verify/resend/",
        resend_verification,
        name="customer_resend_verification",
    ),
    path("login/", CustomerLoginView.as_view(), name="login"),
    path("logout/", CustomerLogoutView.as_view(), name="logout"),
    path(
        "password_reset/",
        CustomerPasswordResetView.as_view(),
        name="password_reset",
    ),
    path(
        "password_reset/done/",
        CustomerPasswordResetDoneView.as_view(),
        name="password_reset_done",
    ),
    path(
        "reset/<uidb64>/<token>/",
        CustomerPasswordResetConfirmView.as_view(),
        name="password_reset_confirm",
    ),
    path(
        "reset/done/",
        CustomerPasswordResetCompleteView.as_view(),
        name="password_reset_complete",
    ),
    path("dashboard/", customer_dashboard, name="customer_dashboard"),
    path("profile/", customer_profile, name="customer_profile"),
    path("quotes/", customer_quote_history, name="customer_quote_history"),
]
