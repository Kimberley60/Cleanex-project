from django.urls import path
from .views import (BookingCreateView,BookingSuccessView,BookingTrackView)



urlpatterns = [
    path("booking/", BookingCreateView.as_view(), name="book"),
    path("booking/success/", BookingSuccessView.as_view(), name="booking_success"),
    path("booking/track/", BookingTrackView.as_view(), name="booking_track"),

]


