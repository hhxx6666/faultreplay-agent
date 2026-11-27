from faultreplay.replay import Adapter

from service import WebhookService


class WebhookReplay:
    def __init__(self, deduplicate):
        self.service = WebhookService(deduplicate)

    def handle(self, event, clock):
        if event.type == "webhook.receive":
            job = self.service.receive(event.payload["delivery_id"], event.payload["topic"])
            if job is not None:
                clock.emit("job.enqueue", job)


BUGGY = Adapter(lambda state: WebhookReplay(deduplicate=False))
FIXED = Adapter(lambda state: WebhookReplay(deduplicate=True))
