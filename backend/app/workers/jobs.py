import dramatiq


@dramatiq.actor
def parse_file_version(file_version_id: str) -> None:
    _ = file_version_id


@dramatiq.actor
def run_review_job(review_job_id: str) -> None:
    _ = review_job_id


@dramatiq.actor
def send_notification(notification_id: str) -> None:
    _ = notification_id

