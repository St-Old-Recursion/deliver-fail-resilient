"""Заглушка отправки email/SMS-уведомлений с вероятностью сбоя ~30%."""
import random
from apps.common.chaos import maybe_fail
from apps.common.exceptions import TemporaryFailure


def send_email(recipient: str, message: str) -> None:
    maybe_fail()


def send_sms(recipient: str, message: str) -> None:
    maybe_fail()
