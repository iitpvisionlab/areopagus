from __future__ import annotations
import secrets
from django.db import models
import uuid
from django.conf import settings
from textwrap import dedent
from django.utils.timezone import now as timezone_now
from django.contrib.auth.models import User
from string import digits
from .jinja2 import localtime
from django.core.mail import get_connection, EmailMessage


SecretaryField = models.ForeignKey(
    User,
    on_delete=models.CASCADE,
    editable=False,
    null=False,
    verbose_name="Секретарь",
)

class Voter(models.Model):
    fio = models.CharField('Ф. И. О.', max_length=1000)
    email = models.EmailField()
    secretary = SecretaryField

    def __str__(self) -> str:
        return self.fio

    class Meta:
        verbose_name = 'Чл. дисс. совета'
        verbose_name_plural = 'Чл. дисс. совета'
        ordering = ('fio',)


def now():
    return timezone_now().replace(microsecond=0,second=0)


def secure_digits(k: int=8, digits: str=digits) -> str:
    return "".join(secrets.choice(digits) for _ in range(k))


class Poll(models.Model):
    title = models.CharField('Соискатель', max_length=1000)
    text = models.CharField('Диссертация', max_length=20000)
    allow_spoiling = models.BooleanField('Разрешить портить бюллетень', default=False)
    date = models.DateTimeField('Дата защиты', default=now, db_index=True)

    voter_local = models.ManyToManyField(Voter, verbose_name='Присутствующие в зале члены совета', related_name='+', blank=True)
    voter_remote = models.ManyToManyField(Voter, verbose_name='Подключённые удалённо члены совета', related_name='+', blank=True)
    secretary = SecretaryField

    class Method(models.TextChoices):
        SIX_DIGITS = '6', '6 цифр'

    private_key_method = models.CharField(
        "Метод получения ключа",
        max_length=1,
        choices=Method.choices,
        default=Method.SIX_DIGITS,
        editable=False,
    )

    class State(models.TextChoices):
        NOT_STARTED = 'N', 'Не начато'
        STARTED = 'S', 'Идёт'
        FINISHED = 'F', 'Завершено'

    state = models.CharField(
        "Состояние",
        max_length=1,
        choices=State.choices,
        default=State.NOT_STARTED,
        editable=False,
    )

    class Meta:
        verbose_name = 'Голосование'
        verbose_name_plural = 'Голосования'
        ordering = ('-date',)
        indexes = [
            # this index exists we filter on date
            models.Index(name="polls_poll__secretary_date_idx", fields=["secretary", "date"]),
        ]

    def __str__(self):
        return f'Голосование №{self.id} [{self.State(self.state).label}]'

    def create_private_key_for(self, send_email: SendEmail) -> Key:
        """
        returns private key and updates `send_email`
        """
        if self.state == Poll.State.FINISHED:
            raise ValueError("Голосование завершено")
        if send_email.visited:
            raise ValueError("Бюллетень уже выдан")

        if self.private_key_method == Poll.Method.SIX_DIGITS:
            for _ in range(1000):
                private_key_value = secure_digits(k=6)
                assert len(private_key_value) == 6, "Метод создания приватного ключа вернул неправильное число цифр."
                private_key, created = Key.objects.get_or_create(poll=self, value=private_key_value)
                if created:
                    break
            else:
                raise ValueError("Невозможно создать приватный ключ. Было произведено 1000 попыток.")
        else:
            raise ValueError(f"Неизвестный метод получения приватного ключа: {self.private_key_method}.")

        send_email.visited = True
        send_email.save(update_fields=["visited"])
        return private_key

    def start_sending_thread(self) -> None:
        """
        Not a thread because threading is quite painful with sqlite
        """
        with get_connection() as email_connection:
            pks = tuple(
                SendEmail.objects.filter(
                    poll = self,
                    status__in = (SendEmail.Status.READY, SendEmail.Status.ERROR, SendEmail.Status.QUEUEING, SendEmail.Status.SENDING),
                ).values_list(
                    'pk', flat=True)
            )
            if not pks:
                return
            SendEmail.objects.filter(pk__in=pks).update(status=SendEmail.Status.QUEUEING)
            for item in SendEmail.objects.filter(pk__in=pks):
                item.status = SendEmail.Status.SENDING
                item.save()
                try:
                    EmailMessage(
                        from_email=settings.DEFAULT_FROM_EMAIL,
                        subject=f"Диссертация ({self.title}): голосование",
                        body=SendEmail.get_message(item, item.public_key),
                        to=[item.voter.email],
                        connection=email_connection,
                    ).send()
                except Exception as e:
                    item.status = SendEmail.Status.ERROR
                    item.info['error'] = repr(e)
                else:
                    item.status = SendEmail.Status.SUCCESS
                item.info['time'] = localtime(timezone_now())
                item.save()



class SendEmail(models.Model):
    info = models.JSONField('Состояние отправки', default=dict, null=False, db_index=True)
    voter = models.ForeignKey(Voter, verbose_name="Чл. дисс. совета", on_delete=models.CASCADE)
    poll = models.ForeignKey(Poll,  verbose_name="Голосование", on_delete=models.CASCADE)
    public_key = models.UUIDField(primary_key=True, default=uuid.uuid4)
    visited = models.BooleanField('Увидел приватный ключ', default=False)
    secretary = SecretaryField

    class Status(models.TextChoices):
        LOCAL = 'L', 'Не нужно отправлять'
        READY = 'R', 'Готово к отправке'
        QUEUEING = 'Q', 'В очереди'
        SENDING = 'P', 'Отправляется'
        SUCCESS = 'S', 'Успешно отправлено'
        ERROR = 'E', 'Ошибка отправки'

    status = models.CharField(
        'Статус',
        max_length=1,
        choices=Status.choices,
        db_index=True,
    )

    class Meta:
        verbose_name_plural = verbose_name = 'Отправка почты'
        constraints = [
            models.UniqueConstraint(fields=['voter', 'poll', 'secretary'], name='voter_poll_unique_idx')
        ]
        ordering = ('poll', 'status', 'visited')

    def __str__(self):
        return f'{self.voter.fio} ({self.voter.email})'

    def url_get_bulletin(self):
        return f"{settings.EMAIL_LINK_START}/get_bulletin/{self.public_key}"

    @staticmethod
    def get_message(item, public_key):
        url_get_bulletin = f"{settings.EMAIL_LINK_START}/get_bulletin/{public_key}"
        header = dedent(f"""
        Здравствуйте, {item.voter.fio}

        Для голосования ({item.poll.title}. {item.poll.text}) перейдите по ссылке:
        {url_get_bulletin.format(public_key=item.public_key)}
        """)
        footer = dedent(f"""
        --
          С уважением,
            Робот диссертационного совета
        """)
        return f"{header}{footer}"


class Key(models.Model):
    poll = models.ForeignKey(Poll, on_delete=models.CASCADE, editable=False, null=True)
    value = models.CharField("Значение приватного ключа", primary_key=True, max_length=1000, editable=False, null=False)

    class Response(models.TextChoices):
        YES = 'Y', 'За'
        NO = 'N', 'Против'
        SPOILED = 'S', 'Недействительно'
        NOT_RETURNED = '?', 'Не вернулось'

    response = models.CharField(
        max_length=2,
        choices=Response.choices,
        default=Response.NOT_RETURNED,
    )

    class Meta:
        indexes = [
            # this index exists because it might make poll statistics faster
            models.Index(name="polls_poll_value_idx", fields=["poll", "response"]),
        ]
        ordering = ('poll', 'value')


    def __str__(self):
        return f'{self.poll} | {self.value} | {self.response}'


class BadKey(models.Model):
    poll = models.ForeignKey(Poll, on_delete=models.CASCADE, null=True)
    value = models.CharField('Переданный ключ', max_length=1000, null=False, db_index=True)
    timestamp = models.DateTimeField('Дата запроса', default=timezone_now)

    class Meta:
        ordering = ('-timestamp',)
