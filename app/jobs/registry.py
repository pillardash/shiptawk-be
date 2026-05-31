from app.jobs.tasks.ai import run_ai_task
from app.jobs.tasks.email import (
    send_invite_email,
    send_password_reset_email,
    send_system_notification_email,
    send_verification_email,
)
from app.jobs.tasks.files import process_uploaded_file
from app.jobs.tasks.notifications import send_notification
from app.jobs.tasks.reports import generate_report
from app.jobs.tasks.webhooks import deliver_webhook
from app.services.jobs.base import TaskCallable

TASK_REGISTRY: dict[str, TaskCallable] = {
    "email.send_password_reset": send_password_reset_email,
    "email.send_verification": send_verification_email,
    "email.send_invite": send_invite_email,
    "email.send_system_notification": send_system_notification_email,
    "files.process_upload": process_uploaded_file,
    "ai.run_task": run_ai_task,
    "reports.generate": generate_report,
    "webhooks.deliver": deliver_webhook,
    "notifications.send": send_notification,
}
