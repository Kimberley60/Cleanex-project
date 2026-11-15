from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import ValidationError
from django.shortcuts import redirect, render
from django.views import View
from django.views.generic import TemplateView
from django.views.decorators.http import require_POST

from apps_core.core.rate_limit import QUOTE_RATE_LIMITS, rate_limited

from .forms import QuoteRequestForm
from .models import QuoteRequest
from .services import (
    accept_quote,
    create_quote_request,
    decline_quote,
)


class QuoteView(View):
    template_name = "pages/get_quote.html"

    def get(self, request):
        return render(
            request,
            self.template_name,
            {"form": QuoteRequestForm()},
        )

    @rate_limited(*QUOTE_RATE_LIMITS)
    def post(self, request):
        form = QuoteRequestForm(data=request.POST)
        if form.is_valid():
            try:
                create_quote_request(
                    form.cleaned_data,
                    user=request.user,
                )
            except ValidationError as error:
                if hasattr(error, "message_dict"):
                    for field, messages in error.message_dict.items():
                        form_field = field if field in form.fields else None
                        for message in messages:
                            form.add_error(form_field, message)
                else:
                    for message in error.messages:
                        form.add_error(None, message)
            else:
                return redirect("quote_success")

        return render(
            request,
            self.template_name,
            {"form": form},
        )


class QuoteSuccessView(TemplateView):
    template_name = "pages/quote_success.html"


@login_required
@require_POST
def accept_customer_quote(request, quote_id):
    try:
        accept_quote(quote_id=quote_id, user=request.user)
    except QuoteRequest.DoesNotExist:
        messages.error(request, "The quotation could not be found.")
    except ValidationError as error:
        messages.error(
            request,
            f"Your quotation could not be accepted: {' '.join(error.messages)}",
        )
    else:
        messages.success(request, "Quotation accepted.")

    return redirect("customer_quote_history")


@login_required
@require_POST
def decline_customer_quote(request, quote_id):
    try:
        decline_quote(quote_id=quote_id, user=request.user)
    except QuoteRequest.DoesNotExist:
        messages.error(request, "The quotation could not be found.")
    except ValidationError as error:
        messages.error(
            request,
            f"Your quotation could not be declined: {' '.join(error.messages)}",
        )
    else:
        messages.success(request, "Quotation declined.")

    return redirect("customer_quote_history")
