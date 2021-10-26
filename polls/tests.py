from django.test import RequestFactory, TestCase, SimpleTestCase
from unittest import mock
from django.contrib.auth.models import User, Permission
from django.test import Client
from polls.models import Voter, Poll, SendEmail, Key
from polls.admin import PollForm  # better to test `PollForm` indirectly
from django.core import mail
from typing import Any, Dict, List, Optional, Sequence, Tuple
import uuid
import re
from django.contrib.admin.helpers import ACTION_CHECKBOX_NAME


def get_admin_view_url(obj: SendEmail, action: str="change") -> str:
    from django.urls import reverse
    return reverse(
        f'admin:{obj._meta.app_label}_{obj.__class__.__name__.lower()}_{action}',
        args=(obj.pk,)
    )

class WithSecretary(TestCase):
    SECRETARY_PASSWORD = "t0p_s3cr3t"
    SECRETARY_USERNAME = "test_secretary"

    @staticmethod
    def _create_secretary(username: str, password: str) -> User:
        secretary: User = User.objects.create_user(
            username=username,
            password=password,
            is_staff=True,
        )
        secretary.user_permissions.add(
            Permission.objects.filter(codename='add_voter').get(),
            Permission.objects.filter(codename='view_voter').get(),
            Permission.objects.filter(codename='change_voter').get(),
            Permission.objects.filter(codename='add_poll').get(),
            Permission.objects.filter(codename='view_poll').get(),
            Permission.objects.filter(codename='change_poll').get(),
            Permission.objects.filter(codename='view_sendemail').get(),
            Permission.objects.filter(codename='change_sendemail').get(),
        )
        return secretary

    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls._secretary = cls._create_secretary(
            username=cls.SECRETARY_USERNAME,
            password=cls.SECRETARY_PASSWORD,
        )

    def setUp(self):
        self.factory = RequestFactory()

    def _secretary_client(self, secretary: Optional[User]=None):
        c = Client()
        c.force_login(user=secretary or self._secretary)
        return c


class WithPoll(WithSecretary):
    @staticmethod
    def _create_voters_for(secretary: User) -> Tuple[List[Voter], List[Voter]]:
        local_voters: List[Voter] = []
        for local_voter_id in range(1, 11):
            local_voters.append(
                Voter.objects.create(
                    fio=f"voter {local_voter_id} local",
                    email=f"voter{local_voter_id}@localhost",
                    secretary=secretary,
                )
            )

        remote_voters: List[Voter] = []
        for remote_voter_id in range(11, 21):
            remote_voters.append(
                Voter.objects.create(
                    fio=f"voter {remote_voter_id} remote",
                    email=f"voter{remote_voter_id}@localhost",
                    secretary=secretary,
                )
            )
        return local_voters, remote_voters

    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        local_voters, remote_voters = cls._create_voters_for(cls._secretary)
        cls.local_voters = local_voters[2:-2]
        cls.remote_voters = remote_voters[2:]

    @classmethod
    def _create_poll(cls, title: str = "POLLTITLE", text: str = "POLLTEXT", **extra: Dict[str, Any]) -> Poll:
        poll = Poll.objects.create(
            title=title,
            text=text,
            secretary=extra.pop("secretary", cls._secretary),
            **extra
        )
        poll.voter_local.set(cls.local_voters)
        poll.voter_remote.set(cls.remote_voters)
        return poll

    def _action(self, action: str, polls: Sequence[Poll], secretary: Optional[User]=None):
        c = self._secretary_client(secretary=secretary)
        post_start = c.post("/secretary/polls/poll/", {
            "action": action,
            ACTION_CHECKBOX_NAME: [poll.pk for poll in polls]
        })
        self.assertRedirects(post_start, "/secretary/polls/poll/")

    def _start_polls(self, polls: Sequence[Poll], secretary: Optional[User]=None):
        self._action("start_poll", polls, secretary)

    def _end_polls(self, polls: Sequence[Poll], secretary: Optional[User]=None):
        self._action("end_poll", polls, secretary)

    def _duplicate_polls(self, polls: Sequence[Poll], secretary: Optional[User]=None):
        self._action("duplicate_poll", polls, secretary)

    def _print_bulletins(self, poll: Poll) -> List[str]:
        c = self._secretary_client()
        response = c.get(f"/secretary/polls/poll/{poll.id}/print/")
        self.assertNotContains(response, "Ошибка:")
        content = response.content.decode()
        return re.findall('<div class="key">(\S+)</div>', content)

    def _go_and_vote_locally(self, poll: Poll, private_keys: List[str], response: List[Key.Response]):
        c = Client()
        private_key = private_keys.pop()
        bulletin_response = c.get(f"/vote/poll_{poll.id}/{private_key}/")
        self.assertEqual(bulletin_response.status_code, 200)
        vote_response = c.post(
            f"/vote/poll_{poll.id}/{private_key}/",
            {"response": [r.value for r in response]},
        )
        self.assertContains(vote_response, "Ваш голос учтён")


class SendEmailTest(WithPoll):
    def test_sendemail_filled(self):
        poll = self._create_poll()
        self.assertEqual(poll.voter_local.count(), len(self.local_voters))
        self.assertEqual(poll.voter_remote.count(), len(self.remote_voters))
        self.assertEqual(SendEmail.objects.filter(poll=poll).count(), 0)
        self._start_polls(Poll.objects.filter(id=poll.id))
        poll.refresh_from_db()
        self.assertEqual(poll.state, Poll.State.STARTED)
        self.assertEqual(
            SendEmail.objects.filter(poll=poll).count(),
            len(self.local_voters) + len(self.remote_voters),
        )

    def test_sendemail_link_in_admin(self):
        poll = self._create_poll()
        self._start_polls([poll])
        c = self._secretary_client()
        response = c.get(get_admin_view_url(SendEmail.objects.all()[0]))
        self.assertContains(response, "Одноразовая ссылка для получения бюллетеня:")

    def test_can_view_all_send_email(self):
        sec1 = self._create_secretary("sec1", "sec1passw0rd")
        sec2 = self._create_secretary("sec2", "sec1passw0rd")
        poll1 = self._create_poll(title="user1", text="text1", secretary=sec1)
        self._start_polls([poll1], secretary=sec1)
        poll1.refresh_from_db()
        poll2 = self._create_poll(title="user2", text="text2", secretary=sec2)
        self._start_polls([poll2], secretary=sec2)
        poll2.refresh_from_db()
        c = self._secretary_client(secretary=sec1)
        polls_response = c.get("/secretary/polls/sendemail/")
        self.assertContains(polls_response, f'href="?poll__id__exact={poll1.pk}"')
        self.assertNotContains(polls_response, f'href="?poll__id__exact={poll2.pk}"')
        response = c.get(f"/secretary/polls/sendemail/?poll__id__exact={poll1.pk}")
        self.assertEqual(response.content.decode().count(str(poll1)), 16)
        self.assertEqual(response.content.decode().count(str(poll2)), 0)

class PollsTest(WithPoll):
    def test_main_page_has_no_polls(self):
        poll = self._create_poll()
        c = Client()
        response = c.get("/")
        self.assertNotContains(response, poll.title)

    def test_start_poll(self):
        poll = self._create_poll()
        self._start_polls(Poll.objects.filter(id=poll.id))
        poll.refresh_from_db()
        self.assertEqual(poll.state, Poll.State.STARTED)

        c = Client()
        response = c.get("/")
        self.assertContains(response, poll.title)

        self._end_polls(Poll.objects.filter(id=poll.id))
        poll.refresh_from_db()
        self.assertEqual(poll.state, Poll.State.FINISHED)

        c = Client()
        response = c.get("/")
        self.assertNotContains(response, poll.title)

    def test_vote_email_closed_poll_404(self):
        poll = self._create_poll()
        c = Client()
        get_bulletin_response = c.get(f"/get_bulletin/{uuid.uuid4()}/")
        self.assertEqual(get_bulletin_response.status_code, 404)

    def test_vote_on_unknown_poll(self):
        c = Client()
        get_result = c.get(f"/vote/poll_42/123456/")
        self.assertContains(get_result, "Данного голосования не существует")
        post_result = c.post(f"/vote/poll_42/123456/")
        self.assertContains(post_result, "Данного голосования не существует")

    def test_results_view_unknown_poll(self):
        c = self._secretary_client()
        results_response = c.get("/secretary/polls/poll/9000/results/")
        self.assertEqual(results_response.status_code, 404)

    def test_results_not_finished(self):
        c = self._secretary_client()
        poll = self._create_poll()
        self._start_polls([poll])
        results_response = c.get(f"/secretary/polls/poll/{poll.id}/results/")
        self.assertContains(results_response, "Голосование не завершено")

    def test_send_email_get_message(self):
        poll = self._create_poll()
        poll.start_sending_thread()
        self.assertEqual(len(mail.outbox), 0)
        self._start_polls([poll])
        poll.start_sending_thread()
        self.assertEqual(len(mail.outbox), len(self.remote_voters))

    @mock.patch("django.core.mail.EmailMessage.send", side_effect=ValueError("faild_send"))
    def test_send_email_exception(self, mocked_send):
        poll = self._create_poll()
        self._start_polls([poll])
        poll.start_sending_thread()
        mocked_send.assert_has_calls([[]] * 6)

    def test_voting_two_times_impossible(self):
        poll = self._create_poll()
        self._start_polls([poll])
        poll.start_sending_thread()
        public_key = re.search("/get_bulletin/(\S+)", mail.outbox[0].body).group(1)
        c = Client()
        get_bulletin_response = c.get(f"/get_bulletin/{public_key}/")
        private_key = re.search(
            '<div class="key">(\S+)</div>', get_bulletin_response.content.decode()
        ).group(1)
        poll_get_response = c.get(f"/vote/poll_{poll.id}/{private_key}/")
        self.assertEqual(poll_get_response.status_code, 200)
        first_vote_response = c.post(
            f"/vote/poll_{poll.id}/{private_key}/", {"response": Key.Response.NO.value}
        )
        self.assertContains(first_vote_response, "Ваш голос учтён")
        second_vote_response = c.post(
            f"/vote/poll_{poll.id}/{private_key}/", {"response": Key.Response.YES.value}
        )
        self.assertContains(second_vote_response, "Ошибка: вы уже проголовали ранее")

    def test_voting_closed_poll(self):
        poll = self._create_poll()
        self._start_polls(Poll.objects.filter(id=poll.id))
        poll.start_sending_thread()
        public_key = re.search("/get_bulletin/(\S+)", mail.outbox[0].body).group(1)
        c = Client()
        get_bulletin_response = c.get(f"/get_bulletin/{public_key}/")
        private_key = re.search(
            '<div class="key">(\S+)</div>', get_bulletin_response.content.decode()
        ).group(1)
        self._end_polls(Poll.objects.filter(id=poll.id))
        poll_get_response = c.get(f"/vote/poll_{poll.id}/{private_key}/")
        self.assertContains(poll_get_response, "Данное голосование завершено")

    def test_voting_invalid_response(self):
        poll = self._create_poll()
        self._start_polls(Poll.objects.filter(id=poll.id))
        poll.start_sending_thread()
        public_key = re.search("/get_bulletin/(\S+)", mail.outbox[0].body).group(1)
        c = Client()
        get_bulletin_response = c.get(f"/get_bulletin/{public_key}/")
        private_key = re.search(
            '<div class="key">(\S+)</div>', get_bulletin_response.content.decode()
        ).group(1)
        poll_get_response = c.post(f"/vote/poll_{poll.id}/{private_key}/", {"response": "TEST"})
        self.assertContains(poll_get_response, "Ответ не распознан")
        poll_get_response = c.post(f"/vote/poll_{poll.id}/{private_key}/", {})
        self.assertContains(poll_get_response, "Ответ не распознан")

    def test_empty_results(self):
        poll = self._create_poll(allow_spoiling=False)
        self._start_polls([poll])
        self._end_polls([poll])
        c = self._secretary_client()
        result = c.get(f'/secretary/polls/poll/{poll.id}/results/')
        self.assertContains(result, ">Всего членов совета: 20<")
        self.assertContains(result, ">Присутствовало на заседании: 14<")
        self.assertContains(result, ">Роздано бюллетеней: 0<")
        self.assertContains(result, ">Осталось нерозданных: 20<")
        self.assertContains(result, ">Оказалось в урне: 0<")
        self.assertContains(result, ">За присуждение степени: 0<")
        self.assertContains(result, ">Против присуждение степени: 0<")

    def test_results_single_vote_yes(self):
        poll = self._create_poll()
        self._start_polls([poll])
        private_keys = self._print_bulletins(poll)
        num_private_keys = len(private_keys)
        self._go_and_vote_locally(poll, private_keys, [Key.Response.YES])
        self._end_polls([poll])
        result = self._secretary_client().get(f'/secretary/polls/poll/{poll.id}/results/')
        self.assertContains(result, ">Всего членов совета: 20<")
        self.assertContains(result, ">Присутствовало на заседании: 14<")
        self.assertContains(result, f">Роздано бюллетеней: {num_private_keys}<")
        self.assertContains(result, f">Осталось нерозданных: {20 - num_private_keys}<")
        self.assertContains(result, ">Оказалось в урне: 1<")
        self.assertContains(result, ">За присуждение степени: 1<")
        self.assertContains(result, ">Против присуждение степени: 0<")

    def test_results_single_vote_no(self):
        poll = self._create_poll()
        self._start_polls([poll])
        private_keys = self._print_bulletins(poll)
        num_private_keys = len(private_keys)
        self._go_and_vote_locally(poll, private_keys, [Key.Response.NO])
        self._end_polls([poll])
        result = self._secretary_client().get(f'/secretary/polls/poll/{poll.id}/results/')
        self.assertContains(result, ">Всего членов совета: 20<")
        self.assertContains(result, ">Присутствовало на заседании: 14<")
        self.assertContains(result, f">Роздано бюллетеней: {num_private_keys}<")
        self.assertContains(result, f">Осталось нерозданных: {20 - num_private_keys}<")
        self.assertContains(result, ">Оказалось в урне: 1<")
        self.assertContains(result, ">За присуждение степени: 0<")
        self.assertContains(result, ">Против присуждение степени: 1<")

    def test_results_single_vote_spoil(self):
        poll = self._create_poll(allow_spoiling=True)
        self._start_polls([poll])
        private_keys = self._print_bulletins(poll)
        num_private_keys = len(private_keys)
        self._go_and_vote_locally(poll, private_keys, [Key.Response.YES, Key.Response.NO])
        self._end_polls([poll])
        result = self._secretary_client().get(f'/secretary/polls/poll/{poll.id}/results/')
        self.assertContains(result, ">Всего членов совета: 20<")
        self.assertContains(result, ">Присутствовало на заседании: 14<")
        self.assertContains(result, f">Роздано бюллетеней: {num_private_keys}<")
        self.assertContains(result, f">Осталось нерозданных: {20 - num_private_keys}<")
        self.assertContains(result, ">Оказалось в урне: 1<")
        self.assertContains(result, ">За присуждение степени: 0<")
        self.assertContains(result, ">Против присуждение степени: 0<")
        self.assertContains(result, ">Недействительных бюллетеней: 1<")

    def test_spoling_not_allowed(self):
        poll = self._create_poll(allow_spoiling=False)
        self._start_polls([poll])
        private_keys = self._print_bulletins(poll)
        c = Client()
        private_key = private_keys.pop()
        for response in (
                [Key.Response.YES, Key.Response.NO],
                Key.Response.SPOILED,
                [Key.Response.SPOILED]
            ):
            vote_response = c.post(
                f"/vote/poll_{poll.id}/{private_key}/", {"response": response},
            )
            self.assertContains(vote_response, "В данном голосовании нельзя портить бюллетень")

    def test_results_single_vote_hackers(self):
        poll = self._create_poll()
        self._start_polls([poll])
        c = Client()
        get_result = c.get(f"/vote/poll_{poll.id}/123456/")
        self.assertContains(
            get_result,
            "Данный номер бюллетеня не зарегистрирован в текущем голосовании",
        )
        post_result = c.post(
            f"/vote/poll_{poll.id}/789012/",
            {"response": Key.Response.YES.value},
        )
        self.assertContains(
            post_result,
            "Данный номер бюллетеня не зарегистрирован в текущем голосовании",
        )
        self._end_polls([poll])
        result = self._secretary_client().get(f'/secretary/polls/poll/{poll.id}/results/')
        self.assertContains(result, ">Всего членов совета: 20<")
        self.assertContains(result, ">Присутствовало на заседании: 14<")
        self.assertContains(result, ">Роздано бюллетеней: 0<")
        self.assertContains(result, ">Осталось нерозданных: 20<")
        self.assertContains(result, ">Оказалось в урне: 0<")
        self.assertContains(result, ">За присуждение степени: 0<")
        self.assertContains(result, ">Против присуждение степени: 0<")
        self.assertContains(
            result, ">Попыток голосовать с некорректным номером бюллетеня: 2<"
        )
        self.assertContains(result, ">123456 в ")
        self.assertContains(result, ">789012 в ")

    def test_admin_poll_form(self):
        base_data = {
            "title": "Соискатель",
            "text": "Тема",
            "password": "1234567",
            "date": "1985-01-02",
        }
        form = PollForm(data=base_data)
        self.assertEqual(form.errors, {})
        self.assertTrue(form.is_valid())

        form = PollForm(data={
            **base_data,
            "voter_local": self.local_voters[0:3],
            "voter_remote": self.remote_voters[0:3],
        })
        self.assertEqual(form.errors, {})
        self.assertTrue(form.is_valid())

        form = PollForm(data={
            **base_data,
            "voter_local": self.local_voters[0:3],
            "voter_remote": self.remote_voters[0:3] + self.local_voters[1:2],
        })
        self.assertEqual(len(form.errors["__all__"]), 1)
        self.assertEqual(form.errors["__all__"][0], "Нельзя одновременно присутствовать в зале и удалённо: ['voter 4 local']")

    def test_secretary_view_polls(self):
        c = self._secretary_client()
        started_poll = self._create_poll(title="started_poll")
        finished_poll = self._create_poll(title="finished_poll")
        self._start_polls([started_poll])
        created_poll = self._create_poll(title="created_poll")
        self._start_polls([finished_poll])
        self._end_polls([finished_poll])
        get_polls_result = c.get("/secretary/polls/poll/")
        self.assertContains(get_polls_result, "started_poll")
        self.assertContains(get_polls_result, "finished_poll")
        self.assertContains(get_polls_result, "created_poll")
        get_created_poll_сhange_result = c.get(get_admin_view_url(created_poll))
        self.assertContains(get_created_poll_сhange_result, "Сохранить и продолжить редактирование")
        get_started_poll_сhange_result = c.get(get_admin_view_url(started_poll))
        self.assertContains(get_started_poll_сhange_result, "Сохранить и продолжить редактирование")
        get_finished_pol_сhange_result = c.get(get_admin_view_url(finished_poll))
        self.assertNotContains(get_finished_pol_сhange_result, "Сохранить и продолжить редактирование")

    def test_duplicate(self):
        poll = self._create_poll()
        self._duplicate_polls([poll])
        self.assertEqual(2, Poll.objects.all().count())


class TranslateTest(WithPoll, WithSecretary, TestCase):
    def test_voter_admin_change_form(self):
        c = self._secretary_client()
        get_voter_request = c.get("/secretary/polls/voter/")
        self.assertNotContains(get_voter_request, 'Удалить выбранных членов диссертационного совета')
        get_voter_add_request = c.get('/secretary/polls/voter/add/')
        self.assertContains(get_voter_add_request, "Добавить члена диссертационного совета")
        post_voter_add_request = c.post('/secretary/polls/voter/add/',
            {'fio': 'Тестовый Голосующий', 'email': 'test@localhost'},
            follow=True
        )
        voter = Voter.objects.get(fio='Тестовый Голосующий')
        self.assertEqual(voter.email, 'test@localhost')
        self.assertContains(post_voter_add_request, "Выберите члена диссертацинного совета для изменения")
        get_voter_change_request = c.get(get_admin_view_url(voter))
        self.assertContains(get_voter_change_request, "Изменить члена диссертационного совета")
        post_voter_change_request = c.post(get_admin_view_url(voter),
            {'fio': voter.fio, 'email': voter.email, '_addanother': "Сохранить+и+добавить+другой+объект"},
            follow=True
        )
        self.assertContains(post_voter_change_request, "Вы можете добавить еще одного члена диссертационного совета ниже.")
        get_voter_delete_request = c.get(get_admin_view_url(voter, 'delete'))
        self.assertEqual(get_voter_delete_request.status_code, 403)  # forbidden


class TestPrint(WithPoll):
    def test_unopened_poll(self):
        poll = self._create_poll()
        c = self._secretary_client()
        response = c.get(f"/secretary/polls/poll/{poll.id}/print/")
        self.assertContains(response, "Голосование не начато")

    def test_print_two_times(self):
        poll = self._create_poll()
        self.assertEqual(0, len(self._print_bulletins(poll)))
        self._start_polls([poll])
        self.assertEqual(6, len(self._print_bulletins(poll)))
        self.assertEqual(0, len(self._print_bulletins(poll)))

    def test_print_404(self):
        c = self._secretary_client()
        response = c.get(f"/secretary/polls/poll/0/print/")
        self.assertEqual(response.status_code, 404)


class TestSuperuser(WithPoll):
    def _superuser_client(self):
        User.objects.create_superuser("admin", "myemail@test", "password123")
        c = Client()
        self.assertTrue(c.login(username="admin", password="password123"))
        return c

    def test_can_view_all_polls(self):
        sec1 = self._create_secretary("sec1", "sec1passw0rd")
        sec2 = self._create_secretary("sec2", "sec1passw0rd")
        poll1 = self._create_poll(title="user1", text="text1", secretary=sec1)
        poll2 = self._create_poll(title="user2", text="text2", secretary=sec2)
        c = self._superuser_client()
        polls_response = c.get("/secretary/polls/poll/")
        self.assertContains(polls_response, poll1.title)
        self.assertContains(polls_response, poll2.title)

    def test_get_actions_translated(self):
        c = self._superuser_client()
        response = c.get('/secretary/polls/voter/')
        self.assertContains(response, "Удалить выбранных членов диссертационного совета")

    def test_can_view_all_send_email(self):
        sec1 = self._create_secretary("sec1", "sec1passw0rd")
        sec2 = self._create_secretary("sec2", "sec1passw0rd")
        poll1 = self._create_poll(title="user1", text="text1", secretary=sec1)
        poll2 = self._create_poll(title="user2", text="text2", secretary=sec2)
        c = self._superuser_client()
        polls_response = c.get("/secretary/polls/sendemail/")
        self.assertContains(polls_response, f'href="?poll__id__exact={poll1.pk}"')
        self.assertContains(polls_response, f'href="?poll__id__exact={poll2.pk}"')


class TestGetBulletin(WithPoll, WithSecretary, TestCase):
    def test_get_bulletin_two_times(self):
        poll = self._create_poll()
        self._start_polls(Poll.objects.filter(id=poll.id))
        poll.start_sending_thread()
        public_key = re.search("/get_bulletin/(\S+)", mail.outbox[0].body).group(1)
        c = Client()
        first_get_bulletin_response = c.get(f"/get_bulletin/{public_key}/")
        first_private_key_re = re.search(
            r'<div class="key">(\S+)</div>', first_get_bulletin_response.content.decode()
        )
        self.assertIsNotNone(first_private_key_re)
        second_get_bulletin_response = c.get(f"/get_bulletin/{public_key}/")
        second_private_key_re = re.search(
            '<div class="key">(\S+)</div>', second_get_bulletin_response.content.decode()
        )
        self.assertIsNone(second_private_key_re)
        self.assertContains(second_get_bulletin_response, "Бюллетень уже выдан")

    def test_get_bulletin_on_closed_poll(self):
        poll = self._create_poll()
        self._start_polls(Poll.objects.filter(id=poll.id))
        poll.start_sending_thread()
        self._end_polls(Poll.objects.filter(id=poll.id))
        public_key = re.search("/get_bulletin/(\S+)", mail.outbox[0].body).group(1)
        c = Client()
        get_bulletin_response = c.get(f"/get_bulletin/{public_key}/")
        private_key_re = re.search(
            '<div class="key">(\S+)</div>', get_bulletin_response.content.decode()
        )
        self.assertIsNone(private_key_re)
        self.assertContains(get_bulletin_response, "Голосование завершено")

    def test_get_bulletin_wrong_private_key_method(self):
        poll = self._create_poll(private_key_method = 'T')
        self._start_polls(Poll.objects.filter(id=poll.id))
        poll.start_sending_thread()
        public_key = re.search("/get_bulletin/(\S+)", mail.outbox[0].body).group(1)
        c = Client()
        get_bulletin_response = c.get(f"/get_bulletin/{public_key}/")
        self.assertContains(get_bulletin_response, "Неизвестный метод получения приватного ключа: T")

    @mock.patch('polls.models.secure_digits', return_value='123456')
    def test_stable_random(self, mock_secure_digits):
        poll = self._create_poll()
        self._start_polls([poll])
        c = self._secretary_client()
        response = c.get(f"/secretary/polls/poll/{poll.id}/print/")
        self.assertContains(response, "Ошибка: Невозможно создать приватный ключ. Было произведено 1000 попыток.")

    @mock.patch('polls.models.secure_digits', return_value='error')
    def test_invalid_return_value(self, mock_secure_digits):
        poll = self._create_poll()
        self._start_polls([poll])
        c = self._secretary_client()
        response = c.get(f"/secretary/polls/poll/{poll.id}/print/")
        self.assertContains(response, "Ошибка: Метод создания приватного ключа вернул неправильное число цифр.")


class MiscellaneousTest(WithSecretary, TestCase):
    def create_test_voter(self):
        return Voter.objects.create(
            fio="фио",
            email="test_voter@localhost",
            secretary=self._secretary,
        )

    def create_test_poll(self):
        return Poll.objects.create(
            title="test poll title",
            text="test poll text",
            secretary=self._secretary,
        )

    def test_send_email_str(self):
        send_email = SendEmail.objects.create(
            voter=self.create_test_voter(),
            poll=self.create_test_poll(),
            secretary=self._secretary,
        )
        self.assertEqual("фио (test_voter@localhost)", str(send_email))

    def test_key_str(self):
        key = Key.objects.create(
            poll=self.create_test_poll(),
            value="123456",
            response=self._secretary,
        )
        self.assertEqual(
            "Голосование №1 [Не начато] | 123456 | test_secretary", str(key)
        )


class UtilityTest(SimpleTestCase):
    def test_wsgi_import(self):
        import iitpvote.wsgi

        self.assertTrue(hasattr(iitpvote.wsgi, "application"))

    def test_asgi_import(self):
        import iitpvote.asgi

        self.assertTrue(hasattr(iitpvote.asgi, "application"))

    def test_manage_main(self):
        from manage import main
        import sys

        old = sys.modules["django.core.management"]
        sys.modules["django.core.management"] = None
        with self.assertRaises(ImportError):
            main()
        sys.modules["django.core.management"] = old
