from django.db import models
from django.contrib.auth.models import User
from django.utils import timezone
from decimal import Decimal


class Turf(models.Model):
    owner = models.ForeignKey(User, on_delete=models.CASCADE, related_name='turfs')
    name = models.CharField(max_length=200)
    description = models.TextField(blank=True)
    location = models.CharField(max_length=300, blank=True)
    sport_type = models.CharField(max_length=100, default='Football')
    open_time = models.TimeField(default='06:00')
    close_time = models.TimeField(default='23:00')
    default_price_per_hour = models.DecimalField(max_digits=8, decimal_places=2, default=800)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return self.name

    class Meta:
        ordering = ['name']


class PricingRule(models.Model):
    DAY_CHOICES = [
        (0,'Monday'),(1,'Tuesday'),(2,'Wednesday'),
        (3,'Thursday'),(4,'Friday'),(5,'Saturday'),(6,'Sunday'),
    ]
    turf = models.ForeignKey(Turf, on_delete=models.CASCADE, related_name='pricing_rules')
    name = models.CharField(max_length=100, blank=True)
    day_of_week = models.IntegerField(choices=DAY_CHOICES, null=True, blank=True)
    specific_date = models.DateField(null=True, blank=True)
    start_time = models.TimeField()
    end_time = models.TimeField()
    price_per_hour = models.DecimalField(max_digits=8, decimal_places=2, null=True, blank=True)
    multiplier = models.DecimalField(max_digits=4, decimal_places=2, null=True, blank=True)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    def get_effective_price(self, default_rate):
        if self.price_per_hour:
            return self.price_per_hour
        if self.multiplier:
            return default_rate * self.multiplier
        return default_rate

    def __str__(self):
        return f"{self.turf.name} - {self.name or 'Rule'}"

    class Meta:
        ordering = ['start_time']


class Customer(models.Model):
    owner = models.ForeignKey(User, on_delete=models.CASCADE, related_name='customers')
    name = models.CharField(max_length=200)
    phone = models.CharField(max_length=20)
    email = models.EmailField(blank=True)
    address = models.TextField(blank=True)
    notes = models.TextField(blank=True)
    total_bookings = models.IntegerField(default=0)
    total_spent = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    last_visit = models.DateField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"{self.name} ({self.phone})"

    @property
    def is_repeat(self):
        return self.total_bookings > 1

    class Meta:
        ordering = ['-total_bookings', 'name']
        unique_together = ['owner', 'phone']


class Booking(models.Model):
    STATUS_CHOICES = [
        ('hold','Hold'),('booked','Booked'),
        ('completed','Completed'),('cancelled','Cancelled'),
    ]
    PAYMENT_STATUS_CHOICES = [
        ('pending','Pending'),('partial','Partial'),
        ('paid','Paid'),('refunded','Refunded'),
    ]

    owner = models.ForeignKey(User, on_delete=models.CASCADE, related_name='bookings')
    turf = models.ForeignKey(Turf, on_delete=models.CASCADE, related_name='bookings')
    customer = models.ForeignKey(Customer, on_delete=models.CASCADE, related_name='bookings')
    booking_date = models.DateField()
    start_time = models.TimeField()
    end_time = models.TimeField()
    total_amount = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    paid_amount = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='booked')
    payment_status = models.CharField(max_length=20, choices=PAYMENT_STATUS_CHOICES, default='pending')
    notes = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"{self.turf.name} — {self.customer.name} ({self.booking_date})"

    @property
    def remaining_amount(self):
        return max(Decimal('0'), self.total_amount - self.paid_amount)

    @property
    def duration_minutes(self):
        from datetime import datetime, timedelta
        s = datetime.combine(self.booking_date, self.start_time)
        e = datetime.combine(self.booking_date, self.end_time)
        if e <= s:
            e += timedelta(days=1)  # crosses midnight (overnight booking)
        return max(0, int((e - s).total_seconds() / 60))

    @property
    def duration_display(self):
        mins = self.duration_minutes
        h, m = divmod(mins, 60)
        if m:
            return f"{h}h {m}m" if h else f"{m}m"
        return f"{h}h"
    
    @property
    def crosses_midnight(self):
        return self.end_time <= self.start_time

    @property
    def end_date(self):
        from datetime import timedelta
        if self.crosses_midnight:
            return self.booking_date + timedelta(days=1)
        return self.booking_date

    def update_payment_status(self):
        if self.paid_amount <= 0:
            self.payment_status = 'pending'
        elif self.paid_amount >= self.total_amount:
            self.payment_status = 'paid'
        else:
            self.payment_status = 'partial'

    class Meta:
        ordering = ['-booking_date', 'start_time']


class Payment(models.Model):
    METHOD_CHOICES = [
        ('cash','Cash'),('upi','UPI'),('card','Card'),
        ('bank_transfer','Bank Transfer'),('other','Other'),
    ]
    booking = models.ForeignKey(Booking, on_delete=models.CASCADE, related_name='payments')
    amount = models.DecimalField(max_digits=10, decimal_places=2)
    method = models.CharField(max_length=20, choices=METHOD_CHOICES, default='cash')
    reference = models.CharField(max_length=200, blank=True)
    note = models.TextField(blank=True)
    paid_at = models.DateTimeField(default=timezone.now)

    class Meta:
        ordering = ['-paid_at']


class BookingLog(models.Model):
    """Per-booking activity: every action recorded here"""
    ACTION_CHOICES = [
        ('created', 'Booking Created'),
        ('status_changed', 'Status Changed'),
        ('payment_added', 'Payment Added'),
        ('amount_changed', 'Amount Changed'),
        ('time_changed', 'Time Changed'),
        ('note_added', 'Note Added'),
        ('cancelled', 'Booking Cancelled'),
    ]
    booking = models.ForeignKey(Booking, on_delete=models.CASCADE, related_name='logs')
    action = models.CharField(max_length=30, choices=ACTION_CHOICES)
    description = models.TextField()
    performed_at = models.DateTimeField(default=timezone.now)

    class Meta:
        ordering = ['performed_at']





class ChatMemory(models.Model):
    """Stores successful chat queries for fuzzy match learning."""
    owner = models.ForeignKey(User, on_delete=models.CASCADE, related_name='chat_memory')
    query_text = models.TextField()              # what user typed
    normalized = models.TextField(db_index=True) # normalized for matching
    intent = models.CharField(max_length=50)
    use_count = models.IntegerField(default=1)
    created_at = models.DateTimeField(auto_now_add=True)
    last_used_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-use_count', '-last_used_at']
        indexes = [
            models.Index(fields=['owner', 'normalized']),
        ]

    def __str__(self):
        return f"{self.intent}: {self.query_text[:40]}"