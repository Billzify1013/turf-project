from django.urls import path
from . import chat_views, views

urlpatterns = [
    path('dashboard/', views.dashboard, name='dashboard'),
    path('', views.landing, name='landing'),
    path('features/', views.features_page, name='features_page'),
    path('pricing/', views.pricing_page, name='pricing_page'),
    path('about/', views.about_page, name='about_page'),
    path('contact/', views.contact_page, name='contact_page'),

    

    path('bookings/', views.booking_list, name='booking_list'),
    path('bookings/create/', views.booking_create, name='booking_create'),
    path('bookings/<int:pk>/', views.booking_detail, name='booking_detail'),
    path('bookings/<int:pk>/status/', views.booking_status, name='booking_status'),
    path('bookings/<int:pk>/payment/', views.add_payment, name='add_payment'),
    path('bookings/<int:pk>/edit/', views.booking_edit, name='booking_edit'),
    path('bookings/<int:pk>/invoice/', views.invoice_view, name='invoice'),
    path('bookings/<int:pk>/pdf/', views.invoice_pdf, name='invoice_pdf'),

    path('customers/', views.customer_list, name='customer_list'),
    path('customers/save/', views.customer_save, name='customer_save'),
    path('customers/<int:pk>/', views.customer_detail, name='customer_detail'),

    path('turfs/', views.turf_list, name='turf_list'),
    path('turfs/save/', views.turf_save, name='turf_save'),
    path('turfs/<int:pk>/delete/', views.turf_delete, name='turf_delete'),

    path('pricing/', views.pricing_list, name='pricing_list'),
    path('pricing/save/', views.pricing_save, name='pricing_save'),
    path('pricing/<int:pk>/delete/', views.pricing_delete, name='pricing_delete'),

    path('analytics/', views.analytics, name='analytics'),
    path('analytics/data/', views.analytics_data, name='analytics_data'),

    path('ajax/price/', views.price_preview, name='price_preview'),
    path('ajax/customer/', views.customer_lookup, name='customer_lookup'),
    path('ajax/slots/', views.booked_slots, name='booked_slots'),

    # Voice assistant
    path('chat/process/', chat_views.chat_process, name='chat_process'),
]
