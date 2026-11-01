"""حزمة النشر متعدد المنصّات."""
from .base import ConsolePublisher, Publisher, PublishResult, build_publisher  # noqa: F401
from .facebook import FacebookPublisher  # noqa: F401
from .telegram import TelegramPublisher  # noqa: F401
from .x import XPublisher  # noqa: F401

__all__ = [
    "Publisher", "PublishResult", "build_publisher", "ConsolePublisher",
    "TelegramPublisher", "FacebookPublisher", "XPublisher",
]
