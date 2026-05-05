from django.contrib import admin
from django.urls import path, include
from django.conf import settings
from django.conf.urls.static import static
from bookings import auth_views



urlpatterns = [
    path('admin/', admin.site.urls),
    path('login/', auth_views.login_view, name='login'),
    path('register/', auth_views.register_view, name='register'),
    path('logout/', auth_views.logout_view, name='logout'),
    path('', include('bookings.urls')),
    path('api/', include('bookings.api_urls')),
] + static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)