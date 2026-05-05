from django.shortcuts import render, get_object_or_404, redirect
from django.contrib.auth.decorators import login_required
from django.http import JsonResponse, HttpResponse
from django.db.models import Sum, Count, Q
from django.utils import timezone
from datetime import datetime, timedelta, date
from decimal import Decimal
import json

from .models import Turf, Booking, Customer, Payment, PricingRule, BookingLog
from .pricing import calculate_price, check_overlap



from django.http import HttpResponse

def robots_txt(request):
    content = """User-agent: *
Allow: /
Disallow: /admin/
Disallow: /api/

Sitemap: https://turfsys.com/sitemap.xml"""
    return HttpResponse(content, content_type='text/plain')

def landing(request):
    if request.user.is_authenticated:
        return redirect('dashboard')
    return render(request, 'public/home.html')

def landing(request):
    if request.user.is_authenticated:
        return redirect('dashboard')
    return render(request, 'public/home.html')


def features_page(request):
    if request.user.is_authenticated:
        return redirect('dashboard')
    return render(request, 'public/features.html')


def pricing_page(request):
    if request.user.is_authenticated:
        return redirect('dashboard')
    return render(request, 'public/pricing.html')


def about_page(request):
    if request.user.is_authenticated:
        return redirect('dashboard')
    return render(request, 'public/about.html')


def contact_page(request):
    if request.user.is_authenticated:
        return redirect('dashboard')

    if request.method == 'POST':
        # Just acknowledge — actual email sending added later
        return JsonResponse({'success': True, 'message': 'Thanks! We will reply within 24 hours.'})

    return render(request, 'public/contact.html')

def get_date_range(request):
    """Parse date range from request, default to TODAY only (timezone-aware)."""
    today = timezone.localtime().date()
    date_from_str = request.GET.get('from', today.isoformat())
    date_to_str = request.GET.get('to', today.isoformat())
    try:
        date_from = datetime.strptime(date_from_str, '%Y-%m-%d').date()
        date_to = datetime.strptime(date_to_str, '%Y-%m-%d').date()
    except:
        date_from = date_to = today
    return date_from, date_to


# ─── DASHBOARD ────────────────────────────────────────────
@login_required
def dashboard(request):
    date_from, date_to = get_date_range(request)
    today = timezone.localtime().date()
    u = request.user

    turfs = Turf.objects.filter(owner=u, is_active=True)

    # Revenue: paid amount in selected date range
    bookings_range = Booking.objects.filter(
        owner=u, booking_date__gte=date_from, booking_date__lte=date_to,
        status__in=['booked', 'completed']
    )
    revenue = bookings_range.aggregate(t=Sum('paid_amount'))['t'] or 0
    total_bookings = bookings_range.count()

    # Pending: only for SELECTED date range (not all future)
    pending_qs = Booking.objects.filter(
        owner=u,
        booking_date__gte=date_from, booking_date__lte=date_to,
        status='booked', payment_status__in=['pending', 'partial']
    )
    pending = sum(b.remaining_amount for b in pending_qs)

    # Future dues separately (after the selected range)
    future_pending_qs = Booking.objects.filter(
        owner=u,
        booking_date__gt=date_to,
        status='booked', payment_status__in=['pending', 'partial']
    )
    future_pending = sum(b.remaining_amount for b in future_pending_qs)

    total_customers = Customer.objects.filter(owner=u).count()

    # Pipeline — for selected date range
    pipeline = {
        'hold': Booking.objects.filter(owner=u, booking_date__gte=date_from, booking_date__lte=date_to, status='hold').count(),
        'booked': Booking.objects.filter(owner=u, booking_date__gte=date_from, booking_date__lte=date_to, status='booked').count(),
        'completed': Booking.objects.filter(owner=u, booking_date__gte=date_from, booking_date__lte=date_to, status='completed').count(),
        'cancelled': Booking.objects.filter(owner=u, booking_date__gte=date_from, booking_date__lte=date_to, status='cancelled').count(),
    }

    recent_bookings = Booking.objects.filter(owner=u).select_related('turf', 'customer').order_by('-created_at')[:10]

    # Multi-day timeline — group by date, then by turf
    from datetime import timedelta as td
    tl_start = 6
    tl_end = 23

    # Build list of dates in range (max 7 to keep UI sane)
    days = []
    cur = date_from
    while cur <= date_to and len(days) < 7:
        days.append(cur)
        cur += td(days=1)

    timeline_days = []  # list of {date, label, turfs: [{id, name, bookings}]}
    for d in days:
        day_data = {
            'date': d.isoformat(),
            'label': d.strftime('%a, %d %b'),
            'is_today': (d == today),
            'turfs': []
        }
        for turf in turfs:
            bks = Booking.objects.filter(
                turf=turf, booking_date=d, status__in=['booked', 'hold', 'completed']
            ).select_related('customer').order_by('start_time')
            bookings_list = []
            for b in bks:
                bookings_list.append({
                    'id': b.id,
                    'customer': b.customer.name,
                    'phone': b.customer.phone,
                    'start': b.start_time.strftime('%H:%M'),
                    'start_12': b.start_time.strftime('%I:%M %p').lstrip('0'),
                    'end': b.end_time.strftime('%H:%M'),
                    'end_12': b.end_time.strftime('%I:%M %p').lstrip('0'),
                    'duration': b.duration_display,
                    'status': b.status,
                    'amount': float(b.total_amount),
                    'paid': float(b.paid_amount),
                    'remaining': float(b.remaining_amount),
                })
                tl_start = min(tl_start, b.start_time.hour)
                end_h = b.end_time.hour + (1 if b.end_time.minute > 0 else 0)
                tl_end = max(tl_end, end_h)
            day_data['turfs'].append({
                'id': turf.id,
                'name': turf.name,
                'sport': turf.sport_type,
                'open': turf.open_time.strftime('%H:%M'),
                'close': turf.close_time.strftime('%H:%M'),
                'bookings': bookings_list,
                'booked_count': len(bookings_list),
            })
        timeline_days.append(day_data)

    tl_start = max(0, tl_start)
    tl_end = min(24, tl_end)

    context = {
        'turfs': turfs, 'today': today,
        'date_from': date_from, 'date_to': date_to,
        'revenue': revenue, 'total_bookings': total_bookings,
        'pending': pending, 'future_pending': future_pending,
        'total_customers': total_customers,
        'pipeline': pipeline, 'recent_bookings': recent_bookings,
        'timeline_days': timeline_days,
        'timeline_json': json.dumps([d for d in timeline_days], default=str),
        'tl_start': tl_start, 'tl_end': tl_end,
    }
    return render(request, 'dashboard/index.html', context)


# ─── BOOKINGS ─────────────────────────────────────────────
@login_required
def booking_list(request):
    date_from, date_to = get_date_range(request)
    u = request.user
    status_f = request.GET.get('status', '')
    turf_f = request.GET.get('turf', '')
    q = request.GET.get('q', '')

    bookings = Booking.objects.filter(
        owner=u, booking_date__gte=date_from, booking_date__lte=date_to
    ).select_related('turf', 'customer')

    if status_f:
        bookings = bookings.filter(status=status_f)
    if turf_f:
        bookings = bookings.filter(turf_id=turf_f)
    if q:
        bookings = bookings.filter(
            Q(customer__name__icontains=q) | Q(customer__phone__icontains=q)
        )

    total_rev = bookings.filter(status__in=['booked','completed']).aggregate(t=Sum('paid_amount'))['t'] or 0
    turfs = Turf.objects.filter(owner=u, is_active=True)

    return render(request, 'bookings/list.html', {
        'bookings': bookings.order_by('-booking_date', 'start_time'),
        'turfs': turfs, 'total_rev': total_rev,
        'date_from': date_from, 'date_to': date_to,
        'filters': {'status': status_f, 'turf': turf_f, 'q': q},
    })


@login_required
def booking_create(request):
    if request.method == 'POST':
        try:
            u = request.user
            turf = get_object_or_404(Turf, id=request.POST['turf_id'], owner=u)
            phone = request.POST['phone'].strip()
            cname = request.POST['customer_name'].strip()
            booking_date = datetime.strptime(request.POST['booking_date'], '%Y-%m-%d').date()
            start_time = datetime.strptime(request.POST['start_time'], '%H:%M').time()
            end_time = datetime.strptime(request.POST['end_time'], '%H:%M').time()

            # Allow overnight: if end <= start, treat as next-day booking
            if start_time == end_time:
                return JsonResponse({'success': False, 'error': 'Start and end time cannot be same.'})
            if check_overlap(turf, booking_date, start_time, end_time):
                return JsonResponse({'success': False, 'error': 'This slot is already booked!'})

            customer, created = Customer.objects.get_or_create(
                owner=u, phone=phone,
                defaults={'name': cname}
            )
            if not created and cname:
                customer.name = cname
                customer.save()

            # Use provided amount or auto-calculate
            provided_amount = request.POST.get('total_amount', '').strip()
            if provided_amount:
                total_amount = Decimal(provided_amount)
            else:
                total_amount = calculate_price(turf, booking_date, start_time, end_time)

            booking = Booking.objects.create(
                owner=u, turf=turf, customer=customer,
                booking_date=booking_date,
                start_time=start_time, end_time=end_time,
                total_amount=total_amount,
                status=request.POST.get('status', 'booked'),
                notes=request.POST.get('notes', ''),
            )

            advance = request.POST.get('advance_payment', '0').strip()
            method = request.POST.get('payment_method', 'cash')
            if advance and float(advance) > 0:
                adv = Decimal(advance)
                Payment.objects.create(booking=booking, amount=adv, method=method)
                booking.paid_amount = adv
                booking.update_payment_status()
                booking.save()

            customer.total_bookings += 1
            customer.total_spent += booking.paid_amount
            customer.last_visit = booking_date
            customer.save()

            BookingLog.objects.create(
                booking=booking, action='created',
                description=f"Booking created — {turf.name} on {booking_date} from {start_time.strftime('%H:%M')} to {end_time.strftime('%H:%M')}. Amount: ₹{total_amount}."
            )
            if booking.paid_amount > 0:
                BookingLog.objects.create(
                    booking=booking, action='payment_added',
                    description=f"Advance payment of ₹{booking.paid_amount} received via {method}."
                )

            return JsonResponse({'success': True, 'booking_id': booking.id, 'total': float(total_amount)})
        except Exception as e:
            return JsonResponse({'success': False, 'error': str(e)})

    turfs = Turf.objects.filter(owner=request.user, is_active=True)
    preselect_turf = request.GET.get('turf', '')
    preselect_phone = request.GET.get('phone', '')
    return render(request, 'bookings/create.html', {
        'turfs': turfs,
        'preselect_turf': preselect_turf,
        'preselect_phone': preselect_phone,
        'today': date.today().isoformat(),
    })


@login_required
def booking_detail(request, pk):
    booking = get_object_or_404(Booking, pk=pk, owner=request.user)
    payments = booking.payments.all()
    logs = booking.logs.all()
    return render(request, 'bookings/detail.html', {
        'booking': booking, 'payments': payments, 'logs': logs,
    })


@login_required
def booking_status(request, pk):
    booking = get_object_or_404(Booking, pk=pk, owner=request.user)
    if request.method != 'POST':
        return JsonResponse({'success': False, 'error': 'Invalid method'})

    try:
        data = json.loads(request.body)
        new = data.get('status', '').strip()
        valid_statuses = ['hold', 'booked', 'completed', 'cancelled']
        if new not in valid_statuses:
            return JsonResponse({'success': False, 'error': f'Invalid status: {new}'})

        old = booking.status
        if old == new:
            return JsonResponse({'success': False, 'error': 'Booking is already in this status'})

        booking.status = new
        booking.save()
        BookingLog.objects.create(
            booking=booking, action='status_changed',
            description=f"Status changed from '{old.title()}' to '{new.title()}'."
        )
        return JsonResponse({'success': True, 'old': old, 'new': new})
    except Exception as e:
        return JsonResponse({'success': False, 'error': str(e)})
    

@login_required
def booking_edit(request, pk):
    """Edit booking date/time/amount with overlap check."""
    booking = get_object_or_404(Booking, pk=pk, owner=request.user)
    if request.method != 'POST':
        return JsonResponse({'success': False, 'error': 'Invalid method'})

    try:
        new_date = datetime.strptime(request.POST['booking_date'], '%Y-%m-%d').date()
        new_start = datetime.strptime(request.POST['start_time'], '%H:%M').time()
        new_end = datetime.strptime(request.POST['end_time'], '%H:%M').time()
        new_amount = Decimal(request.POST.get('total_amount', '0'))

        if new_start == new_end:
            return JsonResponse({'success': False, 'error': 'Start and end time cannot be same.'})

        if new_amount < 0:
            return JsonResponse({'success': False, 'error': 'Amount cannot be negative.'})

        # Check overlap (excluding current booking)
        if check_overlap(booking.turf, new_date, new_start, new_end, exclude_id=booking.id):
            # Suggest available alternatives near requested time
            from datetime import timedelta as td
            suggestions = []
            # Try +30 min, +1hr, +1.5hr, -30 min, -1hr later/earlier
            duration_min = (datetime.combine(new_date, new_end) - datetime.combine(new_date, new_start)).total_seconds() / 60
            if duration_min <= 0:
                duration_min += 1440

            for shift_min in [30, 60, 90, -30, -60, 120, -90]:
                test_start_dt = datetime.combine(new_date, new_start) + td(minutes=shift_min)
                test_end_dt = test_start_dt + td(minutes=duration_min)
                test_start = test_start_dt.time()
                test_end = test_end_dt.time()
                test_date = test_start_dt.date()
                if not check_overlap(booking.turf, test_date, test_start, test_end, exclude_id=booking.id):
                    suggestions.append({
                        'date': test_date.isoformat(),
                        'start': test_start.strftime('%H:%M'),
                        'end': test_end.strftime('%H:%M'),
                        'label': f"{test_start.strftime('%I:%M %p').lstrip('0')} – {test_end.strftime('%I:%M %p').lstrip('0')}"
                    })
                if len(suggestions) >= 3:
                    break
            return JsonResponse({
                'success': False,
                'error': 'This slot is already booked!',
                'suggestions': suggestions
            })

        # Build change log
        changes = []
        if booking.booking_date != new_date:
            changes.append(f"Date: {booking.booking_date.strftime('%d %b %Y')} → {new_date.strftime('%d %b %Y')}")
        if booking.start_time != new_start or booking.end_time != new_end:
            old_t = f"{booking.start_time.strftime('%I:%M %p').lstrip('0')} – {booking.end_time.strftime('%I:%M %p').lstrip('0')}"
            new_t = f"{new_start.strftime('%I:%M %p').lstrip('0')} – {new_end.strftime('%I:%M %p').lstrip('0')}"
            changes.append(f"Time: {old_t} → {new_t}")
        if booking.total_amount != new_amount:
            changes.append(f"Amount: ₹{booking.total_amount} → ₹{new_amount}")

        # Apply changes
        booking.booking_date = new_date
        booking.start_time = new_start
        booking.end_time = new_end
        old_amount = booking.total_amount
        booking.total_amount = new_amount
        booking.update_payment_status()
        booking.save()

        # Log the changes
        if changes:
            BookingLog.objects.create(
                booking=booking,
                action='amount_changed' if booking.total_amount != old_amount else 'time_changed',
                description="Booking updated — " + "; ".join(changes)
            )

        return JsonResponse({
            'success': True,
            'paid': float(booking.paid_amount),
            'remaining': float(booking.remaining_amount),
            'payment_status': booking.payment_status,
        })
    except Exception as e:
        return JsonResponse({'success': False, 'error': str(e)})


@login_required
def add_payment(request, pk):
    booking = get_object_or_404(Booking, pk=pk, owner=request.user)
    if request.method == 'POST':
        try:
            amount = Decimal(request.POST['amount'])
            method = request.POST.get('method', 'cash')
            ref = request.POST.get('reference', '')

            if amount <= 0:
                return JsonResponse({'success': False, 'error': 'Enter valid amount'})
            if booking.paid_amount + amount > booking.total_amount:
                amount = booking.remaining_amount

            Payment.objects.create(booking=booking, amount=amount, method=method, reference=ref)
            booking.paid_amount += amount
            booking.update_payment_status()
            booking.save()
            booking.customer.total_spent += amount
            booking.customer.save()

            BookingLog.objects.create(
                booking=booking, action='payment_added',
                description=f"Payment of ₹{amount} received via {method}{' (Ref: '+ref+')' if ref else ''}."
            )
            return JsonResponse({
                'success': True,
                'paid': float(booking.paid_amount),
                'remaining': float(booking.remaining_amount),
                'payment_status': booking.payment_status,
            })
        except Exception as e:
            return JsonResponse({'success': False, 'error': str(e)})


@login_required
def invoice_view(request, pk):
    booking = get_object_or_404(Booking, pk=pk, owner=request.user)
    payments = booking.payments.all()
    return render(request, 'bookings/invoice.html', {'booking': booking, 'payments': payments})


@login_required
def invoice_pdf(request, pk):
    booking = get_object_or_404(Booking, pk=pk, owner=request.user)
    payments = booking.payments.all()

    try:
        from reportlab.lib.pagesizes import A4
        from reportlab.lib import colors
        from reportlab.lib.units import cm
        from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer
        from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
        import io

        buf = io.BytesIO()
        doc = SimpleDocTemplate(buf, pagesize=A4, topMargin=2*cm, bottomMargin=2*cm, leftMargin=2*cm, rightMargin=2*cm)
        styles = getSampleStyleSheet()
        elements = []

        # Header
        title_style = ParagraphStyle('title', parent=styles['Title'], fontSize=22, textColor=colors.HexColor('#16a34a'), spaceAfter=4)
        elements.append(Paragraph("TurfPro", title_style))
        elements.append(Paragraph(f"Invoice #{booking.id}", styles['Normal']))
        elements.append(Spacer(1, 0.5*cm))

        # Invoice details table
        info_data = [
            ['Booking Date', str(booking.booking_date.strftime('%d %b %Y'))],
            ['Customer', booking.customer.name],
            ['Phone', booking.customer.phone],
            ['Turf', booking.turf.name],
            ['Time Slot', f"{booking.start_time.strftime('%H:%M')} – {booking.end_time.strftime('%H:%M')}"],
            ['Duration', booking.duration_display],
            ['Status', booking.get_status_display()],
        ]
        t = Table(info_data, colWidths=[5*cm, 12*cm])
        t.setStyle(TableStyle([
            ('FONTSIZE', (0,0), (-1,-1), 10),
            ('TEXTCOLOR', (0,0), (0,-1), colors.HexColor('#6b7280')),
            ('FONTNAME', (1,0), (1,-1), 'Helvetica-Bold'),
            ('ROWBACKGROUNDS', (0,0), (-1,-1), [colors.white, colors.HexColor('#f9fafb')]),
            ('BOTTOMPADDING', (0,0), (-1,-1), 6),
            ('TOPPADDING', (0,0), (-1,-1), 6),
        ]))
        elements.append(t)
        elements.append(Spacer(1, 0.5*cm))

        # Amount summary
        amount_data = [
            ['Total Amount', f"Rs. {int(booking.total_amount)}"],
            ['Paid Amount', f"Rs. {int(booking.paid_amount)}"],
            ['Balance Due', f"Rs. {int(booking.remaining_amount)}"],
        ]
        at = Table(amount_data, colWidths=[5*cm, 12*cm])
        at.setStyle(TableStyle([
            ('FONTSIZE', (0,0), (-1,-1), 11),
            ('FONTNAME', (1,0), (1,-1), 'Helvetica-Bold'),
            ('TEXTCOLOR', (1,2), (1,2), colors.HexColor('#dc2626')),
            ('TEXTCOLOR', (1,1), (1,1), colors.HexColor('#16a34a')),
            ('ROWBACKGROUNDS', (0,0), (-1,-1), [colors.white, colors.HexColor('#f0fdf4'), colors.HexColor('#fef2f2')]),
            ('BOTTOMPADDING', (0,0), (-1,-1), 7),
            ('TOPPADDING', (0,0), (-1,-1), 7),
            ('LINEABOVE', (0,2), (-1,2), 1, colors.HexColor('#e5e7eb')),
        ]))
        elements.append(at)
        elements.append(Spacer(1, 0.5*cm))

        # Payment history
        if payments:
            elements.append(Paragraph("Payment History", styles['Heading3']))
            ph_data = [['Date', 'Method', 'Reference', 'Amount']]
            for p in payments:
                ph_data.append([
                    p.paid_at.strftime('%d %b %Y %H:%M'),
                    p.get_method_display(),
                    p.reference or '—',
                    f"Rs. {int(p.amount)}",
                ])
            pt = Table(ph_data, colWidths=[4.5*cm, 3*cm, 6*cm, 3.5*cm])
            pt.setStyle(TableStyle([
                ('BACKGROUND', (0,0), (-1,0), colors.HexColor('#16a34a')),
                ('TEXTCOLOR', (0,0), (-1,0), colors.white),
                ('FONTNAME', (0,0), (-1,0), 'Helvetica-Bold'),
                ('FONTSIZE', (0,0), (-1,-1), 9),
                ('ROWBACKGROUNDS', (0,1), (-1,-1), [colors.white, colors.HexColor('#f9fafb')]),
                ('BOTTOMPADDING', (0,0), (-1,-1), 6),
                ('TOPPADDING', (0,0), (-1,-1), 6),
                ('GRID', (0,0), (-1,-1), 0.5, colors.HexColor('#e5e7eb')),
            ]))
            elements.append(pt)

        elements.append(Spacer(1, cm))
        elements.append(Paragraph("Thank you for choosing TurfPro!", ParagraphStyle('footer', parent=styles['Normal'], textColor=colors.HexColor('#6b7280'), fontSize=9)))

        doc.build(elements)
        buf.seek(0)
        resp = HttpResponse(buf.read(), content_type='application/pdf')
        resp['Content-Disposition'] = f'attachment; filename="invoice_{booking.id}.pdf"'
        return resp
    except ImportError:
        return HttpResponse("Install reportlab: pip install reportlab", status=500)


# ─── CUSTOMERS ────────────────────────────────────────────
@login_required
def customer_list(request):
    q = request.GET.get('q', '')
    customers = Customer.objects.filter(owner=request.user)
    if q:
        customers = customers.filter(Q(name__icontains=q) | Q(phone__icontains=q))
    return render(request, 'customers/list.html', {'customers': customers, 'q': q})


@login_required
def customer_detail(request, pk):
    customer = get_object_or_404(Customer, pk=pk, owner=request.user)

    # Date filter is OPTIONAL — only apply if user explicitly passed `from`/`to`
    user_filter = bool(request.GET.get('from') or request.GET.get('to'))

    all_bookings_qs = customer.bookings.select_related('turf')
    bookings_qs = all_bookings_qs

    date_from = date_to = None
    if user_filter:
        date_from, date_to = get_date_range(request)
        bookings_qs = bookings_qs.filter(
            booking_date__gte=date_from, booking_date__lte=date_to
        )

    bookings = bookings_qs.order_by('-booking_date', '-start_time')

    # Stats — based on FILTERED set (so they update when filter applied)
    active_bks = bookings.filter(status__in=['booked', 'completed'])
    cancelled_bks = bookings.filter(status='cancelled')

    total_due = sum(b.remaining_amount for b in active_bks if b.remaining_amount > 0)
    due_bookings_count = active_bks.filter(payment_status__in=['pending', 'partial']).count()
    cancelled_count = cancelled_bks.count()
    cancelled_amount = sum(b.total_amount for b in cancelled_bks)
    completed_count = active_bks.filter(status='completed').count()

    return render(request, 'customers/detail.html', {
        'customer': customer,
        'bookings': bookings,
        'date_from': date_from, 'date_to': date_to,
        'user_filter': user_filter,
        'total_due': total_due,
        'due_bookings_count': due_bookings_count,
        'cancelled_count': cancelled_count,
        'cancelled_amount': cancelled_amount,
        'completed_count': completed_count,
        'total_in_view': bookings.count(),
    })


@login_required
def customer_save(request):
    if request.method == 'POST':
        try:
            u = request.user
            phone = request.POST['phone'].strip()
            name = request.POST['name'].strip()
            cid = request.POST.get('customer_id', '')
            if cid:
                c = get_object_or_404(Customer, pk=cid, owner=u)
                c.name = name
                c.phone = phone
                c.email = request.POST.get('email', '')
                c.notes = request.POST.get('notes', '')
                c.save()
                return JsonResponse({'success': True, 'customer_id': c.id})
            if Customer.objects.filter(owner=u, phone=phone).exists():
                return JsonResponse({'success': False, 'error': 'Phone already registered'})
            c = Customer.objects.create(
                owner=u, name=name, phone=phone,
                email=request.POST.get('email', ''),
                notes=request.POST.get('notes', ''),
            )
            return JsonResponse({'success': True, 'customer_id': c.id, 'name': c.name})
        except Exception as e:
            return JsonResponse({'success': False, 'error': str(e)})


# ─── TURFS ────────────────────────────────────────────────
@login_required
def turf_list(request):
    turfs = Turf.objects.filter(owner=request.user)
    return render(request, 'turfs/list.html', {'turfs': turfs})


@login_required
def turf_save(request):
    if request.method == 'POST':
        try:
            u = request.user
            tid = request.POST.get('turf_id', '')
            if tid:
                t = get_object_or_404(Turf, pk=tid, owner=u)
            else:
                t = Turf(owner=u)
            t.name = request.POST['name']
            t.sport_type = request.POST.get('sport_type', 'Football')
            t.location = request.POST.get('location', '')
            t.description = request.POST.get('description', '')
            t.open_time = request.POST.get('open_time', '06:00')
            t.close_time = request.POST.get('close_time', '23:00')
            t.default_price_per_hour = Decimal(request.POST.get('default_price', '800'))
            t.save()
            return JsonResponse({'success': True, 'turf_id': t.id})
        except Exception as e:
            return JsonResponse({'success': False, 'error': str(e)})


@login_required
def turf_delete(request, pk):
    t = get_object_or_404(Turf, pk=pk, owner=request.user)
    if request.method == 'POST':
        t.is_active = False
        t.save()
        return JsonResponse({'success': True})


# ─── PRICING ──────────────────────────────────────────────
@login_required
def pricing_list(request):
    turf_f = request.GET.get('turf', '')
    turfs = Turf.objects.filter(owner=request.user, is_active=True)
    rules = PricingRule.objects.filter(turf__owner=request.user, is_active=True).select_related('turf')
    if turf_f:
        rules = rules.filter(turf_id=turf_f)
    return render(request, 'pricing/list.html', {'turfs': turfs, 'rules': rules, 'turf_f': turf_f})


@login_required
def pricing_save(request):
    if request.method == 'POST':
        try:
            turf = get_object_or_404(Turf, id=request.POST['turf_id'], owner=request.user)

            price_str = request.POST.get('price_per_hour', '').strip()
            mult_str = request.POST.get('multiplier', '').strip()
            price_type = request.POST.get('price_type', 'fixed')  # 'fixed' or 'multiplier'

            # Mutually exclusive: only one of price/multiplier
            price_val = None
            mult_val = None
            if price_type == 'fixed':
                if not price_str:
                    return JsonResponse({'success': False, 'error': 'Enter price per hour'})
                price_val = Decimal(price_str)
            elif price_type == 'multiplier':
                if not mult_str:
                    return JsonResponse({'success': False, 'error': 'Enter multiplier value'})
                mult_val = Decimal(mult_str)

            rule_id = request.POST.get('rule_id', '').strip()
            if rule_id:
                # Update existing rule
                rule = get_object_or_404(PricingRule, pk=rule_id, turf__owner=request.user)
                rule.turf = turf
                rule.name = request.POST.get('name', '')
                rule.day_of_week = request.POST.get('day_of_week') or None
                rule.specific_date = request.POST.get('specific_date') or None
                rule.start_time = request.POST['start_time']
                rule.end_time = request.POST['end_time']
                rule.price_per_hour = price_val
                rule.multiplier = mult_val
                rule.save()
            else:
                # Create new rule
                PricingRule.objects.create(
                    turf=turf,
                    name=request.POST.get('name', ''),
                    day_of_week=request.POST.get('day_of_week') or None,
                    specific_date=request.POST.get('specific_date') or None,
                    start_time=request.POST['start_time'],
                    end_time=request.POST['end_time'],
                    price_per_hour=price_val,
                    multiplier=mult_val,
                )
            return JsonResponse({'success': True})
        except Exception as e:
            return JsonResponse({'success': False, 'error': str(e)})


@login_required
def pricing_delete(request, pk):
    r = get_object_or_404(PricingRule, pk=pk, turf__owner=request.user)
    if request.method == 'POST':
        r.delete()
        return JsonResponse({'success': True})


# ─── ANALYTICS ────────────────────────────────────────────
@login_required
def analytics(request):
    date_from, date_to = get_date_range(request)
    return render(request, 'analytics/index.html', {'date_from': date_from, 'date_to': date_to})


@login_required
def analytics_data(request):
    date_from, date_to = get_date_range(request)
    u = request.user

    # Daily revenue in range
    daily = []
    cur = date_from
    while cur <= date_to:
        rev = Booking.objects.filter(
            owner=u, booking_date=cur, status__in=['booked','completed']
        ).aggregate(t=Sum('paid_amount'))['t'] or 0
        daily.append({'date': cur.strftime('%d %b'), 'revenue': float(rev)})
        cur += timedelta(days=1)

    # Hourly utilization
    hourly = {h: 0 for h in range(6, 24)}
    for b in Booking.objects.filter(owner=u, booking_date__gte=date_from, booking_date__lte=date_to, status__in=['booked','completed']):
        for h in range(b.start_time.hour, min(b.end_time.hour + 1, 24)):
            if h in hourly:
                hourly[h] += 1
    hourly_data = [{'hour': f'{h}:00', 'count': hourly[h]} for h in sorted(hourly)]

    # Payment methods
    pay_methods = Payment.objects.filter(
        booking__owner=u, booking__booking_date__gte=date_from, booking__booking_date__lte=date_to
    ).values('method').annotate(total=Sum('amount'), count=Count('id'))

    # Top customers
    top_c = Customer.objects.filter(owner=u).order_by('-total_spent')[:5]

    # Turf utilization
    turf_util = []
    days_count = (date_to - date_from).days + 1
    for turf in Turf.objects.filter(owner=u, is_active=True):
        avail_h = (turf.close_time.hour - turf.open_time.hour) * days_count
        booked = Booking.objects.filter(
            turf=turf, booking_date__gte=date_from, booking_date__lte=date_to,
            status__in=['booked','completed']
        )
        booked_min = sum(b.duration_minutes for b in booked)
        booked_h = booked_min / 60
        pct = round((booked_h / avail_h) * 100, 1) if avail_h > 0 else 0
        turf_util.append({'name': turf.name, 'pct': min(pct, 100), 'booked': round(booked_h, 1)})

    # Summary stats
    all_bookings = Booking.objects.filter(owner=u, booking_date__gte=date_from, booking_date__lte=date_to)
    total_rev = all_bookings.filter(status__in=['booked','completed']).aggregate(t=Sum('paid_amount'))['t'] or 0
    total_pending = all_bookings.filter(status='booked', payment_status__in=['pending','partial']).aggregate(
        t=Sum('total_amount') - Sum('paid_amount')
    )['t'] or 0

    return JsonResponse({
        'daily': daily, 'hourly': hourly_data,
        'pay_methods': list(pay_methods),
        'top_customers': [{'name': c.name, 'spent': float(c.total_spent), 'bookings': c.total_bookings} for c in top_c],
        'turf_util': turf_util,
        'summary': {
            'total_revenue': float(total_rev),
            'total_bookings': all_bookings.filter(status__in=['booked','completed']).count(),
            'pending_amount': float(total_pending),
            'cancelled': all_bookings.filter(status='cancelled').count(),
        }
    })


# ─── AJAX ─────────────────────────────────────────────────
@login_required
def price_preview(request):
    try:
        turf = get_object_or_404(Turf, id=request.GET['turf_id'], owner=request.user)
        booking_date = datetime.strptime(request.GET['date'], '%Y-%m-%d').date()
        start = datetime.strptime(request.GET['start'], '%H:%M').time()
        end = datetime.strptime(request.GET['end'], '%H:%M').time()
        # ✅ debug=True → prints to terminal which rule applies
        price = calculate_price(turf, booking_date, start, end, debug=True)
        overlap = check_overlap(turf, booking_date, start, end)
        return JsonResponse({'price': float(price), 'overlap': overlap})
    except Exception as e:
        import traceback
        traceback.print_exc()
        return JsonResponse({'error': str(e)})


@login_required
def customer_lookup(request):
    phone = request.GET.get('phone', '')
    try:
        c = Customer.objects.get(owner=request.user, phone=phone)
        return JsonResponse({'found': True, 'name': c.name, 'id': c.id, 'bookings': c.total_bookings})
    except Customer.DoesNotExist:
        return JsonResponse({'found': False})


@login_required
def booked_slots(request):
    try:
        turf = get_object_or_404(Turf, id=request.GET['turf_id'], owner=request.user)
        booking_date = datetime.strptime(request.GET['date'], '%Y-%m-%d').date()
        slots = Booking.objects.filter(
            turf=turf, booking_date=booking_date, status__in=['booked','hold']
        ).values('start_time', 'end_time', 'status')
        return JsonResponse({'slots': [
            {'start': s['start_time'].strftime('%H:%M'), 'end': s['end_time'].strftime('%H:%M'), 'status': s['status']}
            for s in slots
        ]})
    except Exception as e:
        return JsonResponse({'error': str(e)})
