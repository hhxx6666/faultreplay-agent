class WebhookService:
    def __init__(self, deduplicate):
        self.deduplicate = deduplicate
        self.seen = set()

    def receive(self, delivery_id, topic):
        if topic != "invoice.paid" or (self.deduplicate and delivery_id in self.seen):
            return None
        self.seen.add(delivery_id)
        return {"delivery_id": delivery_id}
