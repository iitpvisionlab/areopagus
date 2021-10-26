from itertools import groupby
from typing import List, Optional
from django.contrib import admin
from django.contrib.auth.models import User, Group
from django.db import transaction
from django.db.models import Count
from django.db.models.query import QuerySet
from django.http.response import Http404, HttpResponse
from .models import BadKey, Voter, Poll, SendEmail, Key
from django import forms
from django.core.exceptions import ValidationError
from django.utils.html import format_html
from django.contrib.admin import AdminSite
from django.contrib import messages
from django.urls import path
from django.shortcuts import render
from django.db import models
from django.forms import CheckboxSelectMultiple
from django.contrib.auth.admin import UserAdmin, GroupAdmin
from django.conf import settings
from django.contrib import messages
from secrets import SystemRandom
from django.http import HttpRequest


class PollsAdminSite(AdminSite):
    # Text to put at the end of each page's <title>.
    site_title = "Ареопаг"

    # Text to put in each page's <h1> (and above login form).
    site_header = "Ареопаг - Управление голосованиями"

    # Text to put at the top of the admin index page.
    index_title = "Ареопаг"

    def get_urls(self):
        return [
            path(
                "polls/poll/<int:poll_id>/results/", self.admin_view(self.results_view)
            ),
            path(
                "polls/poll/<int:poll_id>/print/", self.admin_view(self.print_bulletins)
            ),
        ] + super().get_urls()

    def print_bulletins(self, request: HttpRequest, poll_id: int, random: SystemRandom=SystemRandom()):
        try:
            poll: Poll = Poll.objects.get(id=poll_id, secretary=request.user)
        except Poll.DoesNotExist:
            raise Http404()
        if poll.state != Poll.State.STARTED:
            return HttpResponse("Голосование не начато")

        with transaction.atomic():
            send_emails = SendEmail.objects.filter(
                visited=False,
                poll=poll,
                status=SendEmail.Status.LOCAL, secretary=request.user)
            private_keys: List[Key] = []
            for send_email in send_emails:
                try:
                    private_keys.append(poll.create_private_key_for(send_email))
                except Exception as e:
                    return HttpResponse(f"Ошибка: {e}")  # hope never happens
            random.shuffle(private_keys)
            return render(
                request,
                "polls/print.html",
                {
                    "settings": settings,
                    "Poll": Poll,
                    "poll": poll,
                    "private_keys": private_keys
                },
            )

    def results_view(self, request: HttpRequest, poll_id: int):
        try:
            poll: Poll = Poll.objects.get(id=poll_id, secretary=request.user)
        except Poll.DoesNotExist:
            raise Http404()
        if poll.state != Poll.State.FINISHED:
            return HttpResponse("Голосование не завершено")
        votes = Key.objects.filter(poll=poll).order_by("response", "value")
        votes = {
            response: list(keys)
            for response, keys in groupby(votes, key=lambda item: item.response)
        }
        attended_count = SendEmail.objects.filter(poll=poll).count()
        bulletins_visited = SendEmail.objects.filter(poll=poll, visited=True).count()
        voters_count = Voter.objects.filter(secretary=poll.secretary).count()
        voted_count = (
            Key.objects.filter(poll=poll)
            .exclude(response=Key.Response.NOT_RETURNED)
            .count()
        )
        bad_key_count = BadKey.objects.filter(poll=poll).count()
        bad_keys_top_100 = BadKey.objects.filter(poll=poll)[0:100]

        return render(
            request,
            "polls/result.html",
            {
                "poll": poll,
                "votes": votes,
                "Key": Key,
                "attended_count": attended_count,
                "bulletins_visited": bulletins_visited,
                "voters_count": voters_count,
                "voted_count": voted_count,
                "bad_key_count": bad_key_count,
                "bad_keys_top_100": bad_keys_top_100,
            },
        )


class LimitForSecretary(admin.ModelAdmin):
    def get_queryset(self, request: HttpRequest):
        qs = super().get_queryset(request)
        if request.user.is_superuser:
            return qs
        return qs.filter(secretary=request.user)

    def save_model(self, request: HttpRequest, obj, form, change):
        obj.secretary = request.user
        super().save_model(request, obj, form, change)


class PollForm(forms.ModelForm):
    class Meta:
        model = Poll
        fields = [
            field.name
            for field in Poll._meta.fields
            if field.editable and field.name not in ()
        ] + ["voter_local", "voter_remote"]

    def clean(self):
        """
        Checks that all the words belong to the sentence's language.
        """
        voter_local = self.cleaned_data.get("voter_local")
        voter_remote = self.cleaned_data.get("voter_remote")
        if voter_local and voter_remote:
            invalid_voters = voter_remote & voter_local
            if invalid_voters:
                names = [voter.fio for voter in invalid_voters]
                raise ValidationError(
                    f"Нельзя одновременно присутствовать в зале и удалённо: {names}"
                )
        return self.cleaned_data


class PollAdmin(LimitForSecretary):
    actions = ['start_poll', 'end_poll', 'duplicate_poll']
    search_fields = ("title",)
    list_filter = ("state",)
    list_display = ("title", "id", "date", "action", "state", "voter_local__count", "voter_remote__count", "voter_voted__count")
    list_display_su = ("title", "id", "date", "state", "voter_local__count", "voter_remote__count", "secretary", "admin_action")
    form = PollForm
    formfield_overrides = {
        models.ManyToManyField: {'widget': CheckboxSelectMultiple},
    }

    def has_delete_permission(self, request: HttpRequest, poll: Optional[Poll]=None):
        return request.user.is_superuser

    def has_change_permission(self, request: HttpRequest, poll: Optional[Poll]=None):
        if poll is not None and poll.state == Poll.State.FINISHED:
            return False
        return super().has_change_permission(request, poll)

    def get_readonly_fields(self, request: HttpRequest, poll: Optional[Poll]=None):
        if poll is not None and poll.state != Poll.State.NOT_STARTED:
            return ('allow_spoiling',)
        return ()

    def has_secretary_permission(self, request: HttpRequest):
        return not request.user.is_superuser

    def get_queryset(self, request: HttpRequest):
        qs = super().get_queryset(request)
        return qs.annotate(
            Count("voter_local", distinct=True), Count("voter_remote", distinct=True)
        )

    @admin.display(description="В зале")
    def voter_local__count(self, obj: Poll) -> int:
        return obj.voter_local__count

    @admin.display(description="Удалённо")
    def voter_remote__count(self, obj: Poll) -> int:
        return obj.voter_remote__count

    @admin.display(description="Проголосовало")
    def voter_voted__count(self, obj: Poll):
        voted = Key.objects.filter(poll=obj).exclude(response=Key.Response.NOT_RETURNED).count()
        return f'{voted} из {obj.voter_local__count + obj.voter_remote__count}'

    def get_list_display(self, request: HttpRequest):
        if request.user.is_superuser:
            return self.list_display_su
        return self.list_display

    @admin.display(description="Действие")
    def admin_action(self, _obj: Poll):
        return 'Только секретарь управляет голосованием'

    @admin.display(description="Действие")
    def action(self, obj: Poll):
        if obj.state == obj.State.NOT_STARTED:
            return "Начните голосование выбрав действие в выпадающем списке"
        if obj.state == obj.State.FINISHED:
            return format_html(
                f"""<a class="button" href="{obj.id}/results/">Результаты голосования</a>"""
            )
        if obj.state == obj.State.STARTED:
            return format_html(
                f"""<a class="button" href="{obj.id}/print/">Печать бюллетеней</a>"""
            )

    def formfield_for_manytomany(self, db_field, request: HttpRequest, **kwargs):
        if db_field.name in ("voter_local", "voter_remote"):
            kwargs["queryset"] = Voter.objects.filter(secretary=request.user)
        return super().formfield_for_foreignkey(db_field, request, **kwargs)

    def get_form(self, request: HttpRequest, obj: Optional[Poll]=None, **kwargs):
        form = super().get_form(request, obj, **kwargs)
        if 'voter_local' in form.base_fields:
            form.base_fields['voter_local'].widget.can_add_related = False
        if 'voter_remote' in form.base_fields:
            form.base_fields['voter_remote'].widget.can_add_related = False
        return form

    @admin.action(description="Запустить голосование", permissions=["secretary"])
    def start_poll(self, request: HttpRequest, queryset: QuerySet[Poll]):
        for poll in queryset.exclude(state=Poll.State.FINISHED):
            with transaction.atomic():
                for voter in poll.voter_remote.all():  # type: Voter
                    SendEmail.objects.get_or_create(
                        voter=voter, poll=poll, secretary=request.user,
                        defaults={'status':SendEmail.Status.READY},
                    )
                for voter in poll.voter_local.all():  # type: Voter
                    SendEmail.objects.get_or_create(
                        voter=voter, poll=poll, secretary=request.user,
                        defaults={'status':SendEmail.Status.LOCAL},
                    )
                poll.state = Poll.State.STARTED
                poll.save()
            poll.start_sending_thread()
            self.message_user(request, f"{poll} успешно запущено", messages.SUCCESS)

    @admin.action(description="Завершить голосование", permissions=["secretary"])
    def end_poll(self, request: HttpRequest, queryset: QuerySet[Poll]):
        with transaction.atomic():
            queryset.filter(state=Poll.State.STARTED).update(state=Poll.State.FINISHED)
            self.message_user(request, "Голосование успешно завершено", messages.SUCCESS)

    @admin.action(description="Дублировать голосование", permissions=["secretary"])
    def duplicate_poll(self, request: HttpRequest, queryset):
        with transaction.atomic():
            for poll in queryset:  # type: Poll
                orig_voter_local: QuerySet[Voter] = poll.voter_local.all()
                orig_voter_remote: QuerySet[Voter] = poll.voter_remote.all()
                poll.pk = None
                poll._state.adding = True
                poll.state = Poll.State.NOT_STARTED
                poll.save()
                poll.voter_local.set(orig_voter_local)
                poll.voter_remote.set(orig_voter_remote)

                self.message_user(request, f"{poll} успешно дублировано", messages.SUCCESS)


class SecretaryPollFilter(admin.SimpleListFilter):
    title = SendEmail.poll.field.verbose_name
    parameter_name = 'poll__id__exact'

    def lookups(self, request: HttpRequest, _model_admin):
        queryset = self.queryset(request, Poll.objects.all())
        ret = [(poll.pk, str(poll)) for poll in queryset.order_by('-date')[0:11]]
        return ret

    def queryset(self, request: HttpRequest, queryset):
        if not request.user.is_superuser:
            queryset = queryset.filter(secretary=request.user)
        if queryset.model is SendEmail:
            pk = self.value()
            if pk is not None:
                queryset = queryset.filter(poll__pk=pk)
        return queryset


class SendEmailAdmin(LimitForSecretary):
    list_display = ("poll", "voter", "status", "visited", "info")
    list_display_su = list_display + ("secretary", )
    list_filter = (SecretaryPollFilter, "status")
    fields = ("poll", "voter", "status", "visited", "info", "secretary", "url_get_bulletin")

    @admin.display(description="Одноразовая ссылка для получения бюллетеня")
    def url_get_bulletin(self, obj: SendEmail):
        return obj.url_get_bulletin()

    def get_list_display(self, request: HttpRequest):
        if request.user.is_superuser:
            return self.list_display_su
        return self.list_display

    def has_delete_permission(self, request: HttpRequest, obj: Optional[SendEmail]=None):
        if not request.resolver_match.url_name:
            return False
        if request.resolver_match.url_name.startswith("polls_sendemail_"):
            return False
        return True  # can be deleted, but not manually

    def has_add_permission(self, request: HttpRequest, obj: Optional[SendEmail]=None):
        return False

    def has_change_permission(self, request: HttpRequest, obj: Optional[SendEmail]=None):
        return False


class VoterAdmin(LimitForSecretary):
    list_display = ("fio", "email")
    search_fields = ("fio",  "email")

    def render_change_form(self, request: HttpRequest, context, add, change, form_url, obj):
        if context["title"] == "Добавить Чл. дисс. совета":
            context["title"] = "Добавить члена диссертационного совета"
        elif context["title"] == "Изменить Чл. дисс. совета":
            context["title"] = "Изменить члена диссертационного совета"
        return super().render_change_form(request, context, add, change, form_url, obj)

    def get_changelist_instance(self, request: HttpRequest):
        changelist = super().get_changelist_instance(request)
        if changelist.title == 'Выберите Чл. дисс. совета для изменения':
            changelist.title = 'Выберите члена диссертацинного совета для изменения'
        return changelist

    def message_user(self, request: HttpRequest, message: str, level: int=messages.INFO, extra_tags="",
                     fail_silently: bool=False):
        if message.endswith("Вы можете добавить еще один Чл. дисс. совета ниже."):
            message = type(message)(message[:-50] +  "Вы можете добавить еще одного члена диссертационного совета ниже.")
        return super().message_user(request, message, level, extra_tags,
                                    fail_silently)

    def get_actions(self, request: HttpRequest):
        actions = super().get_actions(request)
        if 'delete_selected' in actions:  # count(items) > 0 case
            actions['delete_selected'][0].short_description = "Удалить выбранных членов диссертационного совета"
        return actions


admin_site = PollsAdminSite(name="admin")
admin_site.register(Voter, VoterAdmin)
admin_site.register(Poll, PollAdmin)
admin_site.register(SendEmail, SendEmailAdmin)
admin_site.register(User, UserAdmin)
admin_site.register(Group, GroupAdmin)
