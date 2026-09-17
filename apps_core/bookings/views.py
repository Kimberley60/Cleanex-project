from django.shortcuts import render, redirect
from django.views import View
from django.urls import reverse
from apps_core.services.models import Service, ExtraService
from apps_core.customers.models import Customer
from .services import create_booking


class BookingCreateView(View):
    template_name = "components/booking_form.html"

    def get(self, request):
        services = Service.objects.filter(is_active=True)
        extras = ExtraService.objects.filter(is_active=True)

        return render(request, self.template_name, {
            "services": services,
            "extras": extras
        })

    def post(self, request):

        customer, _ = Customer.objects.get_or_create(
            email=request.POST.get("email"),
            defaults={
                "full_name": request.POST.get("full_name"),
                "phone": request.POST.get("phone"),
                "address": request.POST.get("address"),
                "city": request.POST.get("city"),
            }
        )

        service = Service.objects.get(id=request.POST.get("service"))

        extras = ExtraService.objects.filter(
            id__in=request.POST.getlist("extras")
        )

        data = {
            "service": service,
            "booking_date": request.POST.get("booking_date"),
            "booking_time": request.POST.get("booking_time"),
            "address": request.POST.get("address"),
            "city": request.POST.get("city"),
            "property_size": request.POST.get("property_size"),
            "rooms": request.POST.get("rooms"),
            "extras": extras,
            "notes": request.POST.get("notes"),
        }

        booking = create_booking(customer, data)

        return redirect(
            reverse("booking_success") + f"?booking={booking.id}"
        )


class BookingSuccessView(View):

    def get(self, request):
        booking_id = request.GET.get("booking")
        return render(request, "pages/booking_success.html", {
            "booking_id": booking_id
        })


class BookingTrackView(View):

    def get(self, request):
        return render(request, "pages/booking_track.html")