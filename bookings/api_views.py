from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework.permissions import IsAuthenticated
from django.db.models import Sum
from datetime import date
from .models import Booking, Turf, Customer


class BookingsAPI(APIView):
    permission_classes = [IsAuthenticated]
    def get(self, request):
        bks = Booking.objects.filter(owner=request.user).select_related('turf','customer')[:50]
        return Response([{
            'id': b.id, 'turf': b.turf.name, 'customer': b.customer.name,
            'date': str(b.booking_date), 'start': b.start_time.strftime('%H:%M'),
            'end': b.end_time.strftime('%H:%M'), 'total': float(b.total_amount),
            'paid': float(b.paid_amount), 'status': b.status,
        } for b in bks])


class TurfsAPI(APIView):
    permission_classes = [IsAuthenticated]
    def get(self, request):
        turfs = Turf.objects.filter(owner=request.user, is_active=True)
        return Response([{
            'id': t.id, 'name': t.name, 'sport_type': t.sport_type,
            'open': str(t.open_time), 'close': str(t.close_time),
            'price': float(t.default_price_per_hour),
        } for t in turfs])


class CustomersAPI(APIView):
    permission_classes = [IsAuthenticated]
    def get(self, request):
        cs = Customer.objects.filter(owner=request.user)[:50]
        return Response([{
            'id': c.id, 'name': c.name, 'phone': c.phone,
            'bookings': c.total_bookings, 'spent': float(c.total_spent),
        } for c in cs])


class AnalyticsAPI(APIView):
    permission_classes = [IsAuthenticated]
    def get(self, request):
        today = date.today()
        return Response({
            'today_revenue': float(
                Booking.objects.filter(owner=request.user, booking_date=today, status__in=['booked','completed'])
                .aggregate(t=Sum('paid_amount'))['t'] or 0
            ),
            'total_customers': Customer.objects.filter(owner=request.user).count(),
        })
