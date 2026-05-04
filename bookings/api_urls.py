from django.urls import path
from . import api_views

urlpatterns = [
    path('bookings/', api_views.BookingsAPI.as_view()),
    path('turfs/', api_views.TurfsAPI.as_view()),
    path('customers/', api_views.CustomersAPI.as_view()),
    path('analytics/', api_views.AnalyticsAPI.as_view()),
]
