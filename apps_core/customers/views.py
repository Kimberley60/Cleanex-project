import logging

from django.contrib import messages
from django.contrib.auth import get_user_model
from django.contrib.auth.tokens import default_token_generator
from django.contrib.auth.views import (
    LoginView,
    LogoutView,
    PasswordResetCompleteView,
    PasswordResetConfirmView,
    PasswordResetDoneView,
    PasswordResetView,
)
from django.db import IntegrityError
from django.shortcuts import get_object_or_404, redirect, render
from apps_core.core.rate_limit import (
    CUSTOMER_REGISTER_RATE_LIMITS,
    CUSTOMER_RESEND_VERIFICATION_RATE_LIMITS,
    LOGIN_RATE_LIMITS,
    PASSWORD_RESET_RATE_LIMITS,
    rate_limited,
)
from apps_core.notifications.email import send_template_email
from django.urls import reverse, reverse_lazy
from django.utils.encoding import force_bytes, force_str
from django.utils.http import urlsafe_base64_decode, urlsafe_base64_encode
from django.views.decorators.http import require_http_methods
from django.contrib.auth.decorators import login_required

from .forms import (
    CustomerAuthenticationForm,
    CustomerPasswordResetForm,
    CustomerProfileForm,
    CustomerRegistrationForm,
    ResendVerificationForm,
)
from .models import Customer, PendingCustomerRegistration
from .services import (
    AmbiguousCustomerMatch,
    CustomerAccountError,
    create_pending_customer_registration,
    get_customer_dashboard_data,
    update_customer_profile,
    verify_customer_account,
)


logger = logging.getLogger(__name__)
User = get_user_model()


def _send_verification_email(request, user):
    verification_url = request.build_absolute_uri(
        reverse(
            "customer_verify",
            kwargs={
                "uidb64": urlsafe_base64_encode(force_bytes(user.pk)),
                "token": default_token_generator.make_token(user),
            },
        )
    )

    sent_count = send_template_email(
        subject="Verify your CLEANEX account",
        text_template="emails/customer_verification.txt",
        recipient_list=[user.email],
        context={
            "first_name": user.first_name,
            "verification_url": verification_url,
        },
    )

    if sent_count != 1:
        raise RuntimeError("The verification email was not accepted.")



@rate_limited(*CUSTOMER_REGISTER_RATE_LIMITS)
@require_http_methods(["GET", "POST"])
def register_customer(request):
    if request.user.is_authenticated:
        return redirect("customer_profile")

    form = CustomerRegistrationForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        try:
            user = create_pending_customer_registration(
                first_name=form.cleaned_data["first_name"],
                last_name=form.cleaned_data["last_name"],
                email=form.cleaned_data["email"],
                phone=form.cleaned_data["phone"],
                password=form.cleaned_data["password1"],
            )
        except CustomerAccountError as error:
            form.add_error("email", str(error))
        except IntegrityError:
            form.add_error(
                "email",
                "An account with this email address already exists.",
            )
        else:
            try:
                _send_verification_email(request, user)
            except Exception:
                logger.exception(
                    "Could not send verification email for user %s.",
                    user.pk,
                )
                messages.error(
                    request,
                    "Your account is pending verification, but the email "
                    "could not be sent. Please request another verification "
                    "email.",
                )
                return redirect("customer_resend_verification")

            messages.success(
                request,
                "Check your email for a link to verify your CLEANEX account.",
            )
            return redirect("customer_registration_pending")

    return render(
        request,
        "accounts/register.html",
        {"form": form},
    )


def registration_pending(request):
    return render(request, "accounts/registration_pending.html")


@rate_limited(*CUSTOMER_RESEND_VERIFICATION_RATE_LIMITS)
@require_http_methods(["GET", "POST"])
def resend_verification(request):
    form = ResendVerificationForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        email = form.cleaned_data["email"]
        users = list(
            User.objects
            .filter(email__iexact=email, is_active=False)
        )
        if (
            len(users) == 1
            and PendingCustomerRegistration.objects.filter(
                user=users[0]
            ).exists()
        ):
            user = users[0]
            try:
                _send_verification_email(request, user)
            except Exception:
                logger.exception(
                    "Could not resend verification email for user %s.",
                    user.pk,
                )

        messages.success(
            request,
            "If a pending account exists for that address, a verification "
            "email will be sent.",
        )
        return redirect("customer_resend_verification")

    return render(
        request,
        "accounts/resend_verification.html",
        {"form": form},
    )


@require_http_methods(["GET", "POST"])
def verify_customer_email(request, uidb64, token):
    try:
        user_id = force_str(urlsafe_base64_decode(uidb64))
        user = User.objects.get(pk=user_id)
    except (TypeError, ValueError, OverflowError, User.DoesNotExist):
        user = None

    if user is None:
        return render(
            request,
            "accounts/verify_email.html",
            {"valid_link": False},
            status=400,
        )

    if not default_token_generator.check_token(user, token):
        return render(
            request,
            "accounts/verify_email.html",
            {"valid_link": False},
            status=400,
        )

    if user.is_active and Customer.objects.filter(user=user).exists():
        return render(
            request,
            "accounts/verify_email.html",
            {"valid_link": True, "already_verified": True},
        )

    if request.method == "GET":
        return render(
            request,
            "accounts/verify_email.html",
            {"valid_link": True},
        )

    try:
        verify_customer_account(user, token)
    except AmbiguousCustomerMatch as error:
        return render(
            request,
            "accounts/verify_email.html",
            {
                "valid_link": True,
                "verification_error": str(error),
            },
            status=409,
        )
    except CustomerAccountError as error:
        return render(
            request,
            "accounts/verify_email.html",
            {
                "valid_link": False,
                "verification_error": str(error),
            },
            status=400,
        )

    messages.success(
        request,
        "Your email is verified. You can now sign in.",
    )
    return redirect("login")


class CustomerLoginView(LoginView):
    authentication_form = CustomerAuthenticationForm
    template_name = "accounts/login.html"

    @rate_limited(*LOGIN_RATE_LIMITS)
    def post(self, request, *args, **kwargs):
        return super().post(request, *args, **kwargs)

    def form_valid(self, form):
        messages.success(self.request, "You are signed in.")
        return super().form_valid(form)


class CustomerLogoutView(LogoutView):
    def post(self, request, *args, **kwargs):
        response = super().post(request, *args, **kwargs)
        messages.success(request, "You are signed out.")
        return response


class CustomerPasswordResetView(PasswordResetView):
    form_class = CustomerPasswordResetForm
    template_name = "accounts/password_reset_form.html"
    email_template_name = "registration/password_reset_email.txt"
    subject_template_name = "registration/password_reset_subject.txt"
    success_url = reverse_lazy("password_reset_done")

    @rate_limited(*PASSWORD_RESET_RATE_LIMITS)
    def post(self, request, *args, **kwargs):
        return super().post(request, *args, **kwargs)


class CustomerPasswordResetDoneView(PasswordResetDoneView):
    template_name = "accounts/password_reset_done.html"


class CustomerPasswordResetConfirmView(PasswordResetConfirmView):
    template_name = "accounts/password_reset_confirm.html"
    success_url = reverse_lazy("password_reset_complete")


class CustomerPasswordResetCompleteView(PasswordResetCompleteView):
    template_name = "accounts/password_reset_complete.html"


@login_required
def customer_profile(request):
    customer = (
        Customer.objects
        .select_related("user")
        .filter(user=request.user)
        .first()
    )
    if customer is None:
        messages.error(
            request,
            "Your account does not have a customer profile linked yet. "
            "Please contact CLEANEX support so we can review your account.",
        )
        return render(
            request,
            "accounts/profile_unavailable.html",
            status=409,
        )

    form = CustomerProfileForm(
        request.POST or None,
        user=request.user,
        customer=customer,
    )

    if request.method == "POST" and form.is_valid():
        update_customer_profile(
            user=request.user,
            customer=customer,
            data=form.cleaned_data,
        )
        messages.success(request, "Your profile has been updated.")
        return redirect("customer_profile")

    return render(
        request,
        "accounts/profile.html",
        {"form": form},
    )


@login_required
def customer_dashboard(request):
    customer = (
        Customer.objects
        .select_related("user")
        .filter(user=request.user)
        .first()
    )
    if customer is None:
        messages.error(
            request,
            "Your account does not have a customer profile linked yet. "
            "Please contact CLEANEX support so we can review your account.",
        )
        return render(
            request,
            "accounts/profile_unavailable.html",
            status=409,
        )

    dashboard_data = get_customer_dashboard_data(
        user=request.user,
        customer=customer,
    )
    return render(
        request,
        "accounts/dashboard.html",
        dashboard_data,
    )


@login_required
def customer_quote_history(request):
    customer = (
        Customer.objects
        .select_related("user")
        .filter(user=request.user)
        .first()
    )
    if customer is None:
        messages.error(
            request,
            "Your account does not have a customer profile linked yet. "
            "Please contact CLEANEX support so we can review your account.",
        )
        return render(
            request,
            "accounts/profile_unavailable.html",
            status=409,
        )

    quote_requests = customer.quote_requests.select_related("service").all()
    return render(
        request,
        "accounts/quote_history.html",
        {"quote_requests": quote_requests},
    )
