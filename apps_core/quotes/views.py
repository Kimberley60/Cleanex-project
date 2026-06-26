from django.shortcuts import render
from django.views import View
from apps_core.services.models import Service
from .services import calculate_quote


class QuoteView(View):

    template_name = "quotes/quote_form.html"

    def get(self, request):
        services = Service.objects.filter(is_active=True)

        return render(request, self.template_name, {
            "services": services
        })

    def post(self, request):

        service = Service.objects.get(
            id=request.POST.get("service")
        )

        price = calculate_quote(
            service=service,
            property_size=int(request.POST.get("property_size")),
            rooms=int(request.POST.get("rooms")),
            bathrooms=int(request.POST.get("bathrooms")),
            extras=request.POST.get("extras")
        )

        return render(request, self.template_name, {
            "services": Service.objects.all(),
            "price": price
        })