from typing import Dict, List
from django.shortcuts import render
from .models import SendEmail, Key, Poll, BadKey
from django.http import Http404
from django.db import transaction
from django.conf import settings
from django.db.models import Count
from django.views.decorators.cache import never_cache
from django.http import HttpRequest


def index(request: HttpRequest):
    states = {
        item["state"]: item["total"]
        for item in Poll.objects.all().values("state").annotate(total=Count("state"))
    }
    active_polls = list(Poll.objects.filter(state=Poll.State.STARTED))
    return render(
        request,
        "polls/index.html",
        {"states": states, "Poll": Poll, "active_polls": active_polls, 'settings': settings},
    )


def message(request:HttpRequest, message: str, extra: Dict[str, str]={}):
    return render(request, "polls/message.html", {"message": message, **extra})


def get_bulletin_common(request: HttpRequest, send_email: SendEmail, template_name: str, message_extra: Dict[str, str]={}):
    with transaction.atomic():
        poll: Poll = send_email.poll
        try:
            private_key: Key = poll.create_private_key_for(send_email)
        except ValueError as e:
            return message(request, str(e), message_extra)
        url = f"{settings.EMAIL_LINK_START}/vote/poll_{poll.id}/{private_key.value}"
        return render(
            request,
            template_name,
            {"url": url, "settings": settings, "private_key": private_key},
        )


@never_cache
def email_bulletin(request: HttpRequest, public_key: str):
    """
    Show page for getting private key for users with email link
    """
    try:
        send_email = SendEmail.objects.select_related("poll").get(public_key=public_key)
    except SendEmail.DoesNotExist:
        raise Http404()
    return get_bulletin_common(request, send_email, "polls/get_bulletin.html")


@never_cache
def vote(request: HttpRequest, poll_id: int, private_key: str):
    """
    Show the bulletin with vote radio buttons
    """
    try:
        poll = Poll.objects.get(pk=poll_id)
    except Poll.DoesNotExist:
        return message(
            request,
            "Данного голосования не существует",
        )
    if poll.state != Poll.State.STARTED:
        return message(
            request,
            f"Данное голосование { Poll.State(poll.state).label.lower() }",
        )

    with transaction.atomic():
        try:
            key = Key.objects.get(value=private_key, poll=poll_id)
        except Key.DoesNotExist:
            BadKey(value=private_key, poll=poll).save()
            return message(
                request,
                "Данный номер бюллетеня не зарегистрирован в текущем голосовании",
            )
        if request.method == "POST":
            response_list: List[str] = request.POST.getlist("response")
            try:
                responses = {Key.Response(response) for response in response_list}
                if responses == {Key.Response.YES, Key.Response.NO}:
                    response = Key.Response.SPOILED
                elif len(responses) == 1:
                    response = responses.pop()
                else:
                    raise ValueError()
                if not poll.allow_spoiling and response == Key.Response.SPOILED:
                    return message(request, "В данном голосовании нельзя портить бюллетень")
            except ValueError:
                return message(request, "Ответ не распознан")
            if key.response == Key.Response.NOT_RETURNED:  # good user
                key.response = response
                key.save()
                return message(request, "Ваш голос учтён")
        if key.response == key.Response.NOT_RETURNED:
            return render(request, "polls/vote.html", {"key": key, "poll": key.poll})
        else:
            return message(request, "Ошибка: вы уже проголовали ранее")
