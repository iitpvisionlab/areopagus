from django.templatetags.static import static
from django.urls import reverse

from jinja2 import Environment
from django.utils.timezone import get_current_timezone


def localtime(value, timezone = get_current_timezone()):
    return value.astimezone(timezone).strftime("%Y-%m-%d %H:%M:%S")


def environment(**options):
    env = Environment(**options)
    env.globals.update(
        {
            "static": static,
            "url": reverse,
        }
    )
    env.filters.update(
        {
            "localtime": localtime,
        }
    )
    return env
