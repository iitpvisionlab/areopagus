"""iitpvote URL Configuration

The `urlpatterns` list routes URLs to views. For more information please see:
    https://docs.djangoproject.com/en/3.2/topics/http/urls/
Examples:
Function views
    1. Add an import:  from my_app import views
    2. Add a URL to urlpatterns:  path('', views.home, name='home')
Class-based views
    1. Add an import:  from other_app.views import Home
    2. Add a URL to urlpatterns:  path('', Home.as_view(), name='home')
Including another URLconf
    1. Import the include() function: from django.urls import include, path
    2. Add a URL to urlpatterns:  path('blog/', include('blog.urls'))
"""
from django.urls import path, register_converter
from django.urls.converters import StringConverter
from polls.views import email_bulletin, vote, index
from polls.admin import admin_site


class PrivateKeyConverter(StringConverter):
    regex = r'[0-9\-]{1,100}'


register_converter(PrivateKeyConverter, 'pk')


urlpatterns = [
    path('secretary/', admin_site.urls),
    path('', index, name='index'),
    path('get_bulletin/<uuid:public_key>/', email_bulletin, name='email_bulletin'),
    path('vote/poll_<int:poll_id>/<pk:private_key>/', vote, name='vote'),
]
