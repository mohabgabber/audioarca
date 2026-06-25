from django.contrib import admin
from django.urls import path, include
from django.views.generic import TemplateView
from django.contrib.staticfiles.urls import staticfiles_urlpatterns
from django.conf.urls.static import static
from django.conf import settings

urlpatterns = [
    path('robots.txt', TemplateView.as_view(
        template_name="robots.txt", content_type="text/plain"), name="robots.txt"),
    path('', include('dash.urls')),
    path('', include('forensics.urls')),
]

if settings.DEBUG:
    urlpatterns += staticfiles_urlpatterns()
    urlpatterns += [path('admin/', admin.site.urls)]
    urlpatterns += static(settings.MEDIA_URL,
                          document_root=settings.MEDIA_ROOT)
else:
    # Obscure admin URL in production to reduce automated probing
    urlpatterns += [path("admin/ifjdsgdgdfgdfgfgd/dfgdfgfd/r5y5h/fdast4",
                         admin.site.urls)]
