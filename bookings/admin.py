from django.contrib import admin
from .models import Turf, Booking, Customer, Payment, PricingRule, BookingLog
admin.site.register(Turf)
admin.site.register(Booking)
admin.site.register(Customer)
admin.site.register(Payment)
admin.site.register(PricingRule)
admin.site.register(BookingLog)
